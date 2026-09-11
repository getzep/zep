package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"math"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"
	"github.com/getzep/zep-go/v3/option"
	"github.com/joho/godotenv"
	openai "github.com/sashabaranov/go-openai"
)

const (
	zepMaxEpisodeSize = 10000
	openAIModel       = "gpt-4o-mini"
	maxRetries        = 3
)

func chunkDocument(text string, chunkSize, chunkOverlap int) []string {
	if chunkSize <= 0 {
		chunkSize = defaultChunkSize
	}
	if chunkOverlap < 0 {
		chunkOverlap = 0
	}
	if chunkOverlap >= chunkSize {
		chunkOverlap = chunkSize / 4
	}

	paragraphs := regexp.MustCompile(`\n\s*\n`).Split(text, -1)

	var chunks []string
	var currentChunk strings.Builder

	for _, para := range paragraphs {
		para = strings.TrimSpace(para)
		if para == "" {
			continue
		}

		if len(para) > chunkSize {
			sentences := splitIntoSentences(para)
			for _, sentence := range sentences {
				sentence = strings.TrimSpace(sentence)
				if sentence == "" {
					continue
				}

				if currentChunk.Len()+len(sentence)+1 > chunkSize && currentChunk.Len() > 0 {
					chunks = append(chunks, currentChunk.String())
					overlapText := getOverlapText(currentChunk.String(), chunkOverlap)
					currentChunk.Reset()
					currentChunk.WriteString(overlapText)
				}

				if currentChunk.Len() > 0 {
					currentChunk.WriteString(" ")
				}
				currentChunk.WriteString(sentence)
			}
		} else {
			if currentChunk.Len()+len(para)+2 > chunkSize && currentChunk.Len() > 0 {
				chunks = append(chunks, currentChunk.String())
				overlapText := getOverlapText(currentChunk.String(), chunkOverlap)
				currentChunk.Reset()
				currentChunk.WriteString(overlapText)
			}

			if currentChunk.Len() > 0 {
				currentChunk.WriteString("\n\n")
			}
			currentChunk.WriteString(para)
		}
	}

	if currentChunk.Len() > 0 {
		chunks = append(chunks, currentChunk.String())
	}

	return chunks
}

func splitIntoSentences(text string) []string {
	re := regexp.MustCompile(`([.!?]+)\s+`)
	parts := re.Split(text, -1)
	delimiters := re.FindAllString(text, -1)

	var sentences []string
	for i, part := range parts {
		if part == "" {
			continue
		}
		sentence := part
		if i < len(delimiters) {
			sentence += strings.TrimSpace(delimiters[i])
		}
		sentences = append(sentences, sentence)
	}
	return sentences
}

func getOverlapText(text string, overlapSize int) string {
	if overlapSize <= 0 {
		return ""
	}
	if len(text) <= overlapSize {
		return text
	}

	overlap := text[len(text)-overlapSize:]
	spaceIdx := strings.Index(overlap, " ")
	if spaceIdx > 0 && spaceIdx < len(overlap)/2 {
		overlap = overlap[spaceIdx+1:]
	}
	return overlap
}

func contextualizeChunk(ctx context.Context, client *openai.Client, fullDoc, chunk string) (string, error) {
	prompt := fmt.Sprintf(`<document>
%s
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
%s
</chunk>

Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. If the document has a publication date, please include the date in your context. Answer only with the succinct context and nothing else.`, fullDoc, chunk)

	var lastErr error
	for attempt := 0; attempt < maxRetries; attempt++ {
		if attempt > 0 {
			waitTime := time.Duration(math.Pow(2, float64(attempt))) * time.Second
			log.Printf("Rate limited, waiting %v before retry...", waitTime)
			time.Sleep(waitTime)
		}

		resp, err := client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
			Model: openAIModel,
			Messages: []openai.ChatCompletionMessage{
				{Role: openai.ChatMessageRoleUser, Content: prompt},
			},
			MaxTokens: 256,
		})
		if err != nil {
			lastErr = err
			if strings.Contains(err.Error(), "rate limit") || strings.Contains(err.Error(), "429") {
				continue
			}
			return "", fmt.Errorf("OpenAI API error: %w", err)
		}
		if len(resp.Choices) == 0 {
			return "", fmt.Errorf("no response from OpenAI")
		}
		contextText := strings.TrimSpace(resp.Choices[0].Message.Content)
		return fmt.Sprintf("%s\n\n---\n\n%s", contextText, chunk), nil
	}
	return "", fmt.Errorf("max retries exceeded: %w", lastErr)
}

func validateAndTruncate(contextualizedChunk, originalChunk string) string {
	if len(contextualizedChunk) <= zepMaxEpisodeSize {
		return contextualizedChunk
	}

	separator := "\n\n---\n\n"
	maxContextLen := zepMaxEpisodeSize - len(originalChunk) - len(separator)
	if maxContextLen <= 0 {
		log.Printf("Warning: Original chunk exceeds Zep limit, truncating")
		return originalChunk[:zepMaxEpisodeSize]
	}

	parts := strings.SplitN(contextualizedChunk, separator, 2)
	if len(parts) < 2 {
		return contextualizedChunk[:zepMaxEpisodeSize]
	}

	truncatedContext := parts[0]
	if len(truncatedContext) > maxContextLen {
		truncatedContext = truncatedContext[:maxContextLen] + "..."
	}
	return fmt.Sprintf("%s%s%s", truncatedContext, separator, originalChunk)
}

func ensureUserExists(ctx context.Context, client *zepclient.Client, userID string) error {
	_, err := client.User.Get(ctx, userID)
	if err == nil {
		log.Printf("User %s already exists", userID)
		return nil
	}

	log.Printf("Creating user %s", userID)
	_, err = client.User.Add(ctx, &zep.CreateUserRequest{UserID: userID})
	if err != nil {
		return fmt.Errorf("failed to create user: %w", err)
	}
	log.Printf("User %s created successfully", userID)
	return nil
}

func ingestToZep(ctx context.Context, client *zepclient.Client, userID, data string) (string, error) {
	var lastErr error
	for attempt := 0; attempt < maxRetries; attempt++ {
		if attempt > 0 {
			waitTime := time.Duration(math.Pow(2, float64(attempt))) * time.Second
			log.Printf("Retrying Zep ingestion after %v", waitTime)
			time.Sleep(waitTime)
		}

		episode, err := client.Graph.Add(ctx, &zep.AddDataRequest{
			UserID: zep.String(userID),
			Type:   zep.GraphDataTypeText,
			Data:   data,
		})
		if err == nil {
			if episode == nil {
				return "", nil
			}
			return episode.UUID, nil
		}
		lastErr = err
		log.Printf("Zep ingestion attempt %d failed: %v", attempt+1, err)
	}
	return "", fmt.Errorf("max retries exceeded for Zep ingestion: %w", lastErr)
}

func waitForEpisodeProcessing(ctx context.Context, client *zepclient.Client, episodeUUID string) error {
	return WaitForEpisode(ctx, episodeUUID, WaitOptions{
		Timeout:      180 * time.Second,
		PollInterval: 2 * time.Second,
		GetEpisode: func(ctx context.Context, id string) (bool, string, error) {
			ep, err := client.Graph.Episode.Get(ctx, id)
			if err != nil {
				return false, "", err
			}
			processed := ep.Processed != nil && *ep.Processed
			taskID := ""
			if ep.TaskID != nil {
				taskID = *ep.TaskID
			}
			return processed, taskID, nil
		},
		GetTask: func(ctx context.Context, taskID string) (string, string, error) {
			task, err := client.Task.Get(ctx, taskID)
			if err != nil {
				return "", "", err
			}
			status := ""
			if task.Status != nil {
				status = *task.Status
			}
			errMsg := ""
			if task.Error != nil && task.Error.Message != nil {
				errMsg = *task.Error.Message
			}
			return status, errMsg, nil
		},
	})
}

func processDocument(opts Options) error {
	openaiKey := os.Getenv("OPENAI_API_KEY")
	if openaiKey == "" {
		return fmt.Errorf("OPENAI_API_KEY environment variable is required")
	}

	zepAPIKey := os.Getenv("ZEP_API_KEY")
	if !opts.DryRun && zepAPIKey == "" {
		return fmt.Errorf("ZEP_API_KEY environment variable is required (or pass --dry-run)")
	}

	docContent, err := os.ReadFile(opts.Document)
	if err != nil {
		return fmt.Errorf("error reading document: %w", err)
	}
	fullDoc := string(docContent)

	fmt.Println(strings.Repeat("=", 60))
	fmt.Println("DOCUMENT CHUNKING WITH CONTEXTUALIZED RETRIEVAL")
	fmt.Println(strings.Repeat("=", 60))
	fmt.Printf("Document: %s\n", opts.Document)
	fmt.Printf("User ID: %s\n", opts.UserID)
	fmt.Printf("Chunk size: %d\n", opts.ChunkSize)
	fmt.Printf("Chunk overlap: %d\n", opts.ChunkOverlap)
	fmt.Printf("Dry run: %v\n", opts.DryRun)
	fmt.Printf("Wait: %v\n", opts.Wait)

	ctx := context.Background()
	openaiClient := openai.NewClient(openaiKey)

	var zepClient *zepclient.Client
	if !opts.DryRun {
		zepClient = zepclient.NewClient(option.WithAPIKey(zepAPIKey))
		if err := ensureUserExists(ctx, zepClient, opts.UserID); err != nil {
			return fmt.Errorf("error ensuring user exists: %w", err)
		}
	}

	fmt.Printf("\nChunking document (chunk_size=%d, overlap=%d)...\n", opts.ChunkSize, opts.ChunkOverlap)
	chunks := chunkDocument(fullDoc, opts.ChunkSize, opts.ChunkOverlap)
	fmt.Printf("Created %d chunks\n", len(chunks))
	fmt.Println("\nProcessing chunks:")
	fmt.Println(strings.Repeat("-", 60))

	success := 0
	failed := 0
	contextualizedSize := 0

	for i, chunk := range chunks {
		fmt.Printf("\nChunk %d/%d (%d chars)\n", i+1, len(chunks), len(chunk))
		fmt.Println("  Contextualizing with OpenAI...")

		contextualizedChunk, err := contextualizeChunk(ctx, openaiClient, fullDoc, chunk)
		if err != nil {
			fmt.Printf("  ERROR contextualizing: %v\n", err)
			failed++
			continue
		}

		finalChunk := validateAndTruncate(contextualizedChunk, chunk)
		contextualizedSize += len(finalChunk)
		if len(finalChunk) != len(contextualizedChunk) {
			fmt.Printf("  Chunk truncated to fit Zep limit: %d chars\n", len(finalChunk))
		}
		contextEnd := strings.Index(finalChunk, "\n\n---\n\n")
		if contextEnd > 0 {
			previewEnd := contextEnd
			if previewEnd > 100 {
				previewEnd = 100
			}
			fmt.Printf("  Context: %q...\n", finalChunk[:previewEnd])
		}

		if opts.DryRun {
			fmt.Println("  Dry run — skipping Zep ingestion")
			success++
			continue
		}

		fmt.Println("  Ingesting to Zep...")
		episodeUUID, err := ingestToZep(ctx, zepClient, opts.UserID, finalChunk)
		if err != nil {
			fmt.Printf("  ERROR ingesting: %v\n", err)
			failed++
			continue
		}
		fmt.Printf("  Created episode: %s\n", episodeUUID)

		if err := RequireEpisodeUUIDForWait(opts.Wait, episodeUUID); err != nil {
			fmt.Printf("  ERROR waiting: %v\n", err)
			failed++
			continue
		}
		success++

		if opts.Wait {
			fmt.Println("  Waiting for episode processing...")
			if err := waitForEpisodeProcessing(ctx, zepClient, episodeUUID); err != nil {
				fmt.Printf("  ERROR waiting: %v\n", err)
				failed++
				success--
				continue
			}
			fmt.Printf("  Episode %s processed\n", episodeUUID)
		}
	}

	fmt.Println("\n" + strings.Repeat("=", 60))
	fmt.Println("PROCESSING SUMMARY")
	fmt.Println(strings.Repeat("=", 60))
	fmt.Printf("Total chunks: %d\n", len(chunks))
	fmt.Printf("Successfully processed: %d\n", success)
	fmt.Printf("Failed: %d\n", failed)
	fmt.Printf("Original document size: %d characters\n", len(fullDoc))
	fmt.Printf("Total contextualized size: %d characters\n", contextualizedSize)
	if len(fullDoc) > 0 {
		expansion := float64(contextualizedSize-len(fullDoc)) / float64(len(fullDoc)) * 100
		fmt.Printf("Size expansion from contextualization: %.1f%%\n", expansion)
	}
	fmt.Println(strings.Repeat("=", 60))
	return ChunkProcessingExitError(failed)
}

func main() {
	if err := godotenv.Load(); err != nil {
		log.Println("No .env file found, using environment variables")
	}

	opts, err := ParseArgs(os.Args[1:])
	if err != nil {
		if errors.Is(err, errHelp) {
			fmt.Fprint(os.Stdout, usage())
			os.Exit(0)
		}
		fmt.Fprintln(os.Stderr, "Error:", err)
		fmt.Fprint(os.Stderr, usage())
		os.Exit(2)
	}

	if err := processDocument(opts); err != nil {
		log.Fatalf("Error: %v", err)
	}
}
