package memory

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"github.com/redis/go-redis/v9"
	"github.com/spf13/viper"
)

func CreateRedisClient() *redis.Client {
	redisURL := viper.GetString("REDIS_URL")
	if redisURL == "" {
		log.Fatal("$REDIS_URL is not set")
	}
	return redis.NewClient(&redis.Options{
		Addr: redisURL,
	})
}

func EnsureRedisearchIndexIfEnabled(redisClient *redis.Client, longTermMemory bool) {
	if longTermMemory {
		vectorDimensions := 1536
		distanceMetric := "COSINE"

		err := ensureRedisearchIndex(redisClient, vectorDimensions, distanceMetric)
		if err != nil {
			log.Fatalf("RediSearch index error: %v", err)
		}
	}
}

func NewRedisearchResult(values []string) (RedisearchResult, error) {
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
		return RedisearchResult{}, errors.New("missing required fields")
	}

	return RedisearchResult{
		Role:    role,
		Content: content,
		Dist:    dist,
	}, nil
}

func parseRedisearchResponse(response *redis.SliceCmd) ([]RedisearchResult, error) {
	values, err := response.Result()
	if err != nil {
		return nil, err
	}

	results := []RedisearchResult{}

	for i := 1; i < len(values); i++ {
		if subValues, ok := values[i].([]interface{}); ok {
			stringValues := make([]string, len(subValues))

			for i, value := range subValues {
				if s, ok := value.(string); ok {
					stringValues[i] = s
				}
			}

			result, err := NewRedisearchResult(stringValues)
			if err != nil {
				return nil, err
			}

			results = append(results, result)
		}
	}

	return results, nil
}

func ensureRedisearchIndex(
	redisClient *redis.Client,
	vectorDimensions int,
	distanceMetric string,
) error {
	ctx := context.Background()

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
				"zep:",
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
				return err
			}
		} else {
			return err
		}
	}

	return nil
}
