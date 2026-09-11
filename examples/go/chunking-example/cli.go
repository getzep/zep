package main

import (
	"fmt"
	"strconv"
	"strings"
)

const (
	defaultChunkSize    = 6000
	defaultChunkOverlap = 200
)

// Options holds CLI configuration for the chunking example.
type Options struct {
	Document     string
	UserID       string
	ChunkSize    int
	ChunkOverlap int
	DryRun       bool
	Wait         bool
}

// ParseArgs parses chunking-example CLI arguments.
// Accepts a positional document path plus:
//
//	--user-id (required), --chunk-size, --chunk-overlap, --dry-run, --wait
func ParseArgs(args []string) (Options, error) {
	opts := Options{
		ChunkSize:    defaultChunkSize,
		ChunkOverlap: defaultChunkOverlap,
	}

	var positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		switch {
		case arg == "--user-id":
			val, err := needValue(args, &i, arg)
			if err != nil {
				return Options{}, err
			}
			opts.UserID = val
		case strings.HasPrefix(arg, "--user-id="):
			opts.UserID = strings.TrimPrefix(arg, "--user-id=")
		case arg == "--chunk-size":
			val, err := needValue(args, &i, arg)
			if err != nil {
				return Options{}, err
			}
			n, err := strconv.Atoi(val)
			if err != nil || n <= 0 {
				return Options{}, fmt.Errorf("invalid --chunk-size: %q", val)
			}
			opts.ChunkSize = n
		case strings.HasPrefix(arg, "--chunk-size="):
			val := strings.TrimPrefix(arg, "--chunk-size=")
			n, err := strconv.Atoi(val)
			if err != nil || n <= 0 {
				return Options{}, fmt.Errorf("invalid --chunk-size: %q", val)
			}
			opts.ChunkSize = n
		case arg == "--chunk-overlap":
			val, err := needValue(args, &i, arg)
			if err != nil {
				return Options{}, err
			}
			n, err := strconv.Atoi(val)
			if err != nil || n < 0 {
				return Options{}, fmt.Errorf("invalid --chunk-overlap: %q", val)
			}
			opts.ChunkOverlap = n
		case strings.HasPrefix(arg, "--chunk-overlap="):
			val := strings.TrimPrefix(arg, "--chunk-overlap=")
			n, err := strconv.Atoi(val)
			if err != nil || n < 0 {
				return Options{}, fmt.Errorf("invalid --chunk-overlap: %q", val)
			}
			opts.ChunkOverlap = n
		case arg == "--dry-run":
			opts.DryRun = true
		case arg == "--wait":
			opts.Wait = true
		case arg == "--help" || arg == "-h":
			return Options{}, errHelp
		case strings.HasPrefix(arg, "-"):
			return Options{}, fmt.Errorf("unknown flag: %s", arg)
		default:
			positional = append(positional, arg)
		}
	}

	if len(positional) != 1 {
		return Options{}, fmt.Errorf("document path and --user-id are required")
	}
	opts.Document = positional[0]
	if opts.Document == "" || opts.UserID == "" {
		return Options{}, fmt.Errorf("document path and --user-id are required")
	}
	return opts, nil
}

func needValue(args []string, i *int, flag string) (string, error) {
	if *i+1 >= len(args) {
		return "", fmt.Errorf("%s requires a value", flag)
	}
	*i++
	return args[*i], nil
}

var errHelp = fmt.Errorf("help requested")

func usage() string {
	return `Usage: chunking-example <document> --user-id <id> [options]

Chunk a document, contextualize each chunk with OpenAI, and ingest into Zep.

Arguments:
  document                 Path to the document to process

Options:
  --user-id string         Zep user ID for the knowledge graph (required)
  --chunk-size int         Maximum characters per chunk (default 6000)
  --chunk-overlap int      Character overlap between chunks (default 200)
  --dry-run                Process without ingesting to Zep
  --wait                   Wait for processing after each chunk
  -h, --help               Show this help
`
}
