package main

import (
	"context"
	"encoding/binary"
	"fmt"
	"math"

	"github.com/jaevor/go-nanoid"
	"github.com/redis/go-redis/v9"
	openai "github.com/sashabaranov/go-openai"
)

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

func indexMessages(
	messages []MemoryMessage,
	sessionID string,
	openAIClient *openai.Client,
	redisConn *redis.Client,
) error {

	contents := make([]string, len(messages))
	for i, msg := range messages {
		contents[i] = msg.Content
	}

	embeddingRequest := openai.EmbeddingRequest{
		Input: contents,
		Model: openai.AdaEmbeddingV2,
		User:  "your-user-identifier",
	}

	response, err := openAIClient.CreateEmbeddings(context.Background(), embeddingRequest)
	if err != nil {
		return err
	}

	canonicID, err := nanoid.Standard(21)
	if err != nil {
		return err
	}

	for _, data := range response.Data {
		id := canonicID()
		key := fmt.Sprintf("papyrus:%s", id)
		vector := encode(data.Embedding)

		err := redisConn.HSet(context.Background(), key, "session", sessionID, "vector", vector, "content",
			contents[data.Index], "role", messages[data.Index].Role).
			Err()
		if err != nil {
			return err
		}
	}

	return nil
}

func searchMessages(
	query string,
	sessionID string,
	openAIClient *openai.Client,
	redisConn *redis.Client,
) ([]RedisearchResult, error) {
	embeddingRequest := openai.EmbeddingRequest{
		Input: []string{query},
		Model: openai.AdaEmbeddingV2,
		User:  "your-user-identifier",
	}

	response, err := openAIClient.CreateEmbeddings(context.Background(), embeddingRequest)
	if err != nil {
		return nil, err
	}

	vector := encode(response.Data[0].Embedding)
	searchQuery := fmt.Sprintf("@session:%s=>[KNN 10 @vector $V AS dist]", sessionID)

	searchCmd := searchRedisConn(redisConn, searchQuery, vector)
	err = redisConn.Process(context.Background(), searchCmd)
	if err != nil {
		return nil, err
	}

	results, err := parseRedisearchResponse(searchCmd)
	if err != nil {
		return nil, err
	}

	return results, nil
}

func searchRedisConn(redisConn *redis.Client, searchQuery string, vector []byte) *redis.SliceCmd {
	ctx := context.Background()
	cmd := redis.NewSliceCmd(
		ctx,
		"FT.SEARCH",
		"papyrus",
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
	redisConn.Process(ctx, cmd)
	return cmd
}
