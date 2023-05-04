package memorystore

import (
	"context"
	"encoding/binary"
	"fmt"
	"math"
	"strconv"
	"strings"
	"sync"

	"github.com/danielchalef/zep/pkg/llms"
	"github.com/jaevor/go-nanoid"

	"github.com/danielchalef/zep/pkg/models"
	"github.com/redis/go-redis/v9"
	"github.com/spf13/viper"
)

// Force compiler to validate that RedisMemoryStore implements the MemoryStore interface.
var _ models.MemoryStore[*redis.Client] = &RedisMemoryStore{}

type RedisMemoryStore struct {
	models.BaseMemoryStore[*redis.Client]
}

// NewRedisMemoryStore creates a new RedisMemoryStore. Use this to correctly initialize the store.
func NewRedisMemoryStore(
	appState *models.AppState,
	client *redis.Client,
) (*RedisMemoryStore, error) {
	rms := &RedisMemoryStore{models.BaseMemoryStore[*redis.Client]{Client: client}}
	err := rms.OnStart(context.Background(), appState)
	if err != nil {
		return nil, NewStorageError("failed to run OnInit", err)
	}
	return rms, nil
}

// NewDefaultRedisMemoryStore creates a new RedisMemoryStore with a default redis client initialized with settings
// from the config file.
func NewDefaultRedisMemoryStore(appState *models.AppState) (*RedisMemoryStore, error) {
	client := createRedisClient()
	return NewRedisMemoryStore(appState, client)
}

// OnStart is called when the application starts and creates the redisearch index.
func (rms *RedisMemoryStore) OnStart(
	_ context.Context,
	appState *models.AppState,
) error {
	// TODO: check vectorDimensions and metric values are valid. Think we should use an enum for metric
	if appState.Embeddings.Enabled {
		distanceMetric := viper.GetString("memory.search.metric")
		return ensureRedisearchIndex(
			rms.Client,
			appState.Embeddings.Dimensions,
			distanceMetric,
		)
	}

	return nil
}

// GetMemory retrieves the last N messages and summary for a session. If lastNMessages is 0 then
// all messages are returned. LastNTokens is currently not implemented.
func (rms *RedisMemoryStore) GetMemory(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
	lastNMessages int64,
	lastNTokens int64,
) (*models.MessageResponse, error) {
	summaryKey := fmt.Sprintf("%s_summary", sessionID)
	tokenCountKey := fmt.Sprintf("%s_tokens", sessionID)
	keys := []string{summaryKey, tokenCountKey}

	if lastNTokens > 0 {
		return nil, NewStorageError("not implemented", nil)
	}

	if lastNMessages > 0 && lastNTokens > 0 {
		return nil, NewStorageError("cannot specify both lastNMessages and lastNTokens", nil)
	}

	if lastNMessages < 0 || lastNTokens < 0 {
		return nil, NewStorageError("cannot specify negative lastNMessages or lastNTokens", nil)
	}

	pipe := rms.Client.Pipeline()
	lrangeCmd := pipe.LRange(ctx, sessionID, 0, lastNMessages-1)
	mgetCmd := pipe.MGet(ctx, keys...)
	_, err := pipe.Exec(ctx)
	if err != nil {
		return nil, NewStorageError("failed to execute GetMemory pipeline", err)
	}

	messages, err := lrangeCmd.Result()
	if err != nil {
		return nil, NewStorageError("failed to get lrange result", err)
	}

	values, err := mgetCmd.Result()
	if err != nil {
		return nil, NewStorageError("failed to get mget result", err)
	}

	summary, _ := values[0].(string)
	tokensString, _ := values[1].(string)
	tokens, _ := strconv.ParseInt(tokensString, 10, 64)

	memoryMessages := make([]models.Message, len(messages))
	for i, message := range messages {
		parts := strings.SplitN(message, ": ", 2)
		if len(parts) == 2 {
			memoryMessages[len(messages)-1-i] = models.Message{ // reverse the order
				Role:    parts[0],
				Content: parts[1],
			}
		} else {
			return nil, NewStorageError(fmt.Sprintf("failed to parse message %s", message), nil)
		}
	}

	response := models.MessageResponse{
		Messages: memoryMessages,
		Summary:  models.Summary{Content: summary},
		Tokens:   tokens,
	}

	return &response, nil
}

// GetSummary retrieves the summary from Redis.
func (rms *RedisMemoryStore) GetSummary(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
) (*models.Summary, error) {
	summary, err := rms.Client.Get(ctx, fmt.Sprintf("%s_summary", sessionID)).Result()
	if err == redis.Nil {
		return nil, NewStorageError("summary not found", nil)
	} else if err != nil {
		return nil, NewStorageError("failed to get summary", err)
	}
	return &models.Summary{Content: summary}, nil
}

// PutMemory stores the memory messages and summary in Redis. It also creates embeddings if embeddings are enabled,
// and lazy trims the session to the configured MaxSessionLength.
func (rms *RedisMemoryStore) PutMemory(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	memoryMessages *models.MessagesAndSummary,
	wg *sync.WaitGroup,
) error {

	messages := make([]string, len(memoryMessages.Messages))
	for i, memoryMessage := range memoryMessages.Messages {
		messages[i] = fmt.Sprintf("%s: %s", memoryMessage.Role, memoryMessage.Content)
	}

	if memoryMessages.Summary.Content != "" {
		err := rms.PutSummary(ctx, appState, sessionID, &memoryMessages.Summary)
		if err != nil {
			return NewStorageError(
				"failed to set summary",
				err,
			)
		}
	}

	res, err := rms.Client.LPush(ctx, sessionID, messages).Result()
	if err != nil {
		return NewStorageError(
			"failed to lpush on RedisMemoryStore put",
			err,
		)
	}

	if appState.Embeddings.Enabled {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := rms.GenerateEmbeddings(appState, sessionID, &memoryMessages.Messages); err != nil {
				log.Error("error in GenerateEmbeddings", err)
			}
		}()
	}

	// If MaxSessionLength is set and we have more than the MaxSessionLength of messages, prune the oldest
	if appState.MaxSessionLength > 0 && res > appState.MaxSessionLength {
		wg.Add(1)
		go func() {
			defer wg.Done()
			log.Info(
				fmt.Sprintf("running prune to limit to %d messages", appState.MaxSessionLength),
			)
			if err := rms.PruneSession(context.Background(), appState, sessionID, appState.MaxSessionLength, true); err != nil {
				log.Error("error in pruning session", err)
			}
		}()
	}
	return nil
}

func (rms *RedisMemoryStore) PutSummary(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
	summary *models.Summary,
) error {
	err := rms.Client.Set(ctx, fmt.Sprintf("%s_summary", sessionID), summary.Content, 0).Err()
	if err != nil {
		return NewStorageError(
			"failed to set summary",
			err,
		)
	}

	return nil
}

// PruneSession trims the session to the specified number of messages.
func (rms *RedisMemoryStore) PruneSession(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	messageCount int64,
	lockSession bool,
) error {
	// Optionally lock the session to prevent concurrent prune operations
	if lockSession {
		sessionLock, _ := appState.SessionLock.LoadOrStore(sessionID, &sync.Mutex{})
		sessionLockMutex := sessionLock.(*sync.Mutex)
		sessionLockMutex.Lock()
		defer sessionLockMutex.Unlock()
	}

	err := rms.Client.LTrim(ctx, sessionID, 0, messageCount-1).Err()
	if err != nil {
		return NewStorageError(
			"failed to ltrim on prune",
			err,
		)
	}

	return nil
}

// DeleteSession deletes the session from redis. Note that it does not currently delete the embeddings.
// TODO: delete embeddings as well
func (rms *RedisMemoryStore) DeleteSession(ctx context.Context, sessionID string) error {
	summaryKey := fmt.Sprintf("%s_summary", sessionID)
	tokenCountKey := fmt.Sprintf("%s_tokens", sessionID)

	keys := []string{summaryKey, sessionID, tokenCountKey}

	_, err := rms.Client.Del(ctx, keys...).Result()
	if err != nil {
		return err
	}

	return nil
}

// SearchMemory searches the redis index for the given query
func (rms *RedisMemoryStore) SearchMemory(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	query *models.SearchPayload,
) (*[]models.SearchResult, error) {
	if query.Text == "" {
		return nil, NewStorageError("no search text provided", nil)
	}

	embeddings, err := llms.EmbedMessages(ctx, appState, &[]string{query.Text})
	if err != nil {
		return nil, NewStorageError("failed to embed query", err)
	}

	vector := encode((*embeddings)[0].Embedding)
	searchCmd, err := rms.searchRedisConn(ctx, sessionID, vector)
	if err != nil {
		return nil, NewStorageError("failed to execute search", err)
	}

	searchResults, err := parseRedisearchResponse(searchCmd)
	if err != nil {
		return nil, NewStorageError("failed to parse search results", err)
	}

	return searchResults, nil
}

// searchRedisConn executes a Redisearch query and returns the raw response as a SliceCmd
func (rms *RedisMemoryStore) searchRedisConn(
	ctx context.Context,
	sessionID string,
	vector []byte,
) (*redis.SliceCmd, error) {
	searchQuery := fmt.Sprintf("@session:%s=>[KNN 10 @vector $V AS dist]", sessionID)
	cmd := redis.NewSliceCmd(
		ctx,
		"FT.SEARCH",
		"zep",
		searchQuery,
		"PARAMS",
		"2",
		"V",
		vector,
		"RETURN",
		"3",
		"role",
		"content",
		"dist",
		"SORTBY",
		"dist",
		"DIALECT",
		"2",
	)
	err := rms.Client.Process(ctx, cmd)
	if err != nil {
		return nil, NewStorageError("redis search failed", err)
	}
	return cmd, nil
}

// parseRedisearchResponse parses the redisearch response into a slice of SearchResult structs
func parseRedisearchResponse(response *redis.SliceCmd) (*[]models.SearchResult, error) {
	values, err := response.Result()
	if err != nil {
		return nil, NewStorageError("failed to get redisearch result", err)
	}

	results := make([]models.SearchResult, 0, len(values)-1)

	for i := 1; i < len(values); i++ {
		subValues, ok := values[i].([]interface{})
		if !ok {
			continue
		}

		stringValues := make([]string, len(subValues))

		for i, value := range subValues {
			s, ok := value.(string)
			if ok {
				stringValues[i] = s
			}
		}

		result, err := NewSearchResult(stringValues)
		if err != nil {
			return nil, NewStorageError("failed to parse redisearch result", err)
		}

		results = append(results, *result)
	}

	return &results, nil
}

// NewSearchResult creates a SearchResult from a slice of strings, extracting the
// role, content, and distance fields.
func NewSearchResult(values []string) (*models.SearchResult, error) {
	var content, role string
	var dist float64

	for i := 0; i < len(values); i++ {
		switch values[i] {
		case "content":
			content = values[i+1]
		case "role":
			role = values[i+1]
		case "dist":
			dist, _ = strconv.ParseFloat(values[i+1], 64)
		}
	}

	if role == "" || content == "" {
		return &models.SearchResult{}, NewStorageError("missing required fields", nil)
	}

	return &models.SearchResult{
		Role:    role,
		Content: content,
		Dist:    dist,
	}, nil
}

// GenerateEmbeddings generates embeddings for the messages in the session
// and stores them in redis. runs in a goroutine.
func (rms *RedisMemoryStore) GenerateEmbeddings(appState *models.AppState,
	sessionID string, messages *[]models.Message) error {
	contents := make([]string, len(*messages))
	for i, msg := range *messages {
		contents[i] = msg.Content
	}

	embeddings, err := llms.EmbedMessages(context.Background(), appState, &contents)
	if err != nil {
		return NewStorageError("error generating embeddings", err)
	}

	idGen, err := nanoid.Standard(21)
	if err != nil {
		return NewStorageError("error generating nanoid", err)
	}

	for _, data := range *embeddings {
		id := idGen()
		key := fmt.Sprintf("zep:%s", id)
		vector := encode(data.Embedding)

		err := rms.Client.HSet(context.Background(), key, "session", sessionID, "vector", vector, "content",
			contents[data.Index], "role", (*messages)[data.Index].Role).
			Err()
		if err != nil {
			return NewStorageError("error persisting embeddings to redis", err)
		}
	}

	return nil
}

func createRedisClient() *redis.Client {
	redisURL := viper.GetString("datastore.url")
	if redisURL == "" {
		log.Fatal("datastore.url is not set")
	}
	return redis.NewClient(&redis.Options{
		Addr: redisURL,
	})
}

// ensureRedisearchIndex ensures that the redisearch index exists
func ensureRedisearchIndex(
	redisClient *redis.Client,
	vectorDimensions int64,
	distanceMetric string,
) error {
	ctx := context.Background()

	// TODO: move to config
	indexName := "zep"

	indexInfoCmd := redisClient.Do(ctx, "FT.INFO", indexName)
	_, err := indexInfoCmd.Result()

	if err != nil {
		if strings.Contains(err.Error(), "Unknown Index name") {
			args := []interface{}{
				indexName,
				"ON",
				"HASH",
				"PREFIX",
				"1",
				fmt.Sprintf("%s:", indexName),
				"SCHEMA",
				"session",
				"TEXT",
				"content",
				"TEXT",
				"role",
				"TEXT",
				"vector",
				"VECTOR",
				"HNSW",
				"6",
				"TYPE",
				"FLOAT32",
				"DIM",
				fmt.Sprint(vectorDimensions),
				"DISTANCE_METRIC",
				distanceMetric,
			}

			cmdArgs := []interface{}{"FT.CREATE"}
			cmdArgs = append(cmdArgs, args...)

			_, err = redisClient.Do(ctx, cmdArgs...).Result()
			if err != nil {
				return NewStorageError("failed to create redis index", err)
			}

			return nil
		}

		if err != nil {
			return NewStorageError("failed to query for presence of redis index", err)
		}
	}

	return nil
}

// encode takes a slice of float32 numbers (fs) and converts each number
// to its IEEE 754 binary representation stored as a slice of bytes in little-endian order.
// The resulting byte slice is returned.
func encode(fs []float32) []byte {
	buf := make([]byte, 4*len(fs))
	for i, f := range fs {
		binary.LittleEndian.PutUint32(buf[i*4:], math.Float32bits(f))
	}
	return buf
}
