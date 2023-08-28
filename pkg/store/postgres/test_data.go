package postgres

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/uptrace/bun/extra/bundebug"

	"github.com/brianvoe/gofakeit/v6"
	"github.com/getzep/zep/pkg/models"
	"github.com/google/uuid"
	"github.com/uptrace/bun"
	"github.com/uptrace/bun/dbfixture"
	"gopkg.in/yaml.v3"
)

type Row interface {
	UserSchema | SessionSchema | DocumentCollectionSchema
}

type FixtureModel[T Row] struct {
	Model string `yaml:"model"`
	Rows  []T    `yaml:"rows"`
}

type Fixtures[T Row] []FixtureModel[T]

func generateTimeLastNDays(nDays int) time.Time {
	now := time.Now()
	twoWeeksAgo := now.Add(time.Duration(-nDays) * 24 * time.Hour)
	return gofakeit.DateRange(twoWeeksAgo, now)
}

func generateTestTableName(collectionName string, embeddingDims int) string {
	nameSlug := strings.ToLower(strings.ReplaceAll(collectionName, " ", "_"))
	return fmt.Sprintf(
		"docstore_%s_%d",
		nameSlug,
		embeddingDims,
	)
}

func GenerateFixtureData(fixtureCount int, outputDir string) {
	gofakeit.Seed(0)

	// Generate test data for UserSchema
	users := make([]UserSchema, fixtureCount)
	for i := 0; i < fixtureCount; i++ {
		dateCreated := generateTimeLastNDays(14)
		users[i] = UserSchema{
			UUID:      uuid.New(),
			CreatedAt: dateCreated,
			UpdatedAt: dateCreated,
			UserID:    gofakeit.Username(),
			Email:     gofakeit.Email(),
			FirstName: gofakeit.FirstName(),
			LastName:  gofakeit.LastName(),
		}
	}
	// Generate test data for SessionSchema
	var sessions []SessionSchema
	for i := 0; i < fixtureCount; i++ {
		sessionCount := gofakeit.Number(1, fixtureCount)
		for j := 0; j < sessionCount; j++ {
			dateCreated := generateTimeLastNDays(14)
			sessions = append(sessions, SessionSchema{
				UUID:      uuid.New(),
				SessionID: gofakeit.UUID(),
				CreatedAt: dateCreated,
				UpdatedAt: dateCreated,
				UserID:    &users[i].UserID,
			})
		}
	}

	// Generate test data for DocumentCollection
	collections := make([]DocumentCollectionSchema, fixtureCount)
	embeddingDimensions := []int{384, 768, 1536}

	for i := 0; i < fixtureCount; i++ {
		gofakeit.ShuffleInts(embeddingDimensions)
		dateCreated := generateTimeLastNDays(14)
		collectionName := strings.ToLower(gofakeit.HackerNoun() + gofakeit.AchAccount())
		tableName := generateTestTableName(collectionName, embeddingDimensions[0])

		collections[i] = DocumentCollectionSchema{
			DocumentCollection: models.DocumentCollection{
				UUID:                uuid.New(),
				CreatedAt:           dateCreated,
				UpdatedAt:           dateCreated,
				Name:                collectionName,
				Description:         gofakeit.BookTitle(),
				Metadata:            map[string]interface{}{"key": gofakeit.Word()},
				TableName:           tableName,
				EmbeddingModelName:  gofakeit.Word(),
				EmbeddingDimensions: embeddingDimensions[0],
				IsAutoEmbedded:      gofakeit.Bool(),
				DistanceFunction:    "cosine",
				IsNormalized:        gofakeit.Bool(),
				IsIndexed:           gofakeit.Bool(),
				ListCount:           gofakeit.Number(1, 100),
				ProbeCount:          gofakeit.Number(1, 100),
			},
		}
	}

	userFixture := Fixtures[UserSchema]{
		{
			Model: "UserSchema",
			Rows:  users,
		},
	}

	sessionFixture := Fixtures[SessionSchema]{
		{
			Model: "SessionSchema",
			Rows:  sessions,
		},
	}

	collectionFixture := Fixtures[DocumentCollectionSchema]{
		{
			Model: "DocumentCollectionSchema",
			Rows:  collections,
		},
	}

	if outputDir == "" {
		outputDir = "./"
	} else {
		// Create output directory if it doesn't exist
		if _, err := os.Stat(outputDir); os.IsNotExist(err) {
			err = os.Mkdir(outputDir, 0755)
			if err != nil {
				fmt.Printf("unable to create %s: %v", outputDir, err)
				return
			}
		}
	}

	// Write fixtures to YAML files
	writeFixtureToYAML(userFixture, outputDir, "user_fixtures.yaml")
	writeFixtureToYAML(sessionFixture, outputDir, "session_fixtures.yaml")
	writeFixtureToYAML(collectionFixture, outputDir, "collection_fixtures.yaml")
}

func writeFixtureToYAML[T Row](fixtures Fixtures[T], outputDir, filename string) {
	// Marshal the fixture into YAML
	data, err := yaml.Marshal(&fixtures)
	if err != nil {
		fmt.Printf("error: %v", err)
		return
	}

	// Write the YAML data to a file
	file, err := os.Create(filepath.Join(outputDir, filename))
	if err != nil {
		fmt.Printf("error: %v", err)
		return
	}
	defer file.Close()

	_, err = file.Write(data)
	if err != nil {
		fmt.Printf("error: %v", err)
		return
	}

	fmt.Printf("Fixtures generated successfully in %s!\n", filename)
}

func createTestDocumentTables(ctx context.Context, db *bun.DB) error {
	type Result struct {
		TableName           string `bun:"table_name"`
		EmbeddingDimensions int    `bun:"embedding_dimensions"`
	}

	var results []Result

	// Query DocumentCollections to get all table names and embedding dimensions
	err := db.NewSelect().
		Model((*DocumentCollectionSchema)(nil)).
		Column("table_name", "embedding_dimensions").
		Scan(ctx, &results)
	if err != nil {
		return fmt.Errorf("failed to query DocumentCollections: %w", err)
	}

	// Create tables for each DocumentCollection
	for _, table := range results {
		err = createDocumentTable(ctx, db, table.TableName, table.EmbeddingDimensions)
		if err != nil {
			return fmt.Errorf("failed to create table %s: %w", table.TableName, err)
		}
	}

	return nil
}

func LoadFixtures(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
	fixturePath string,
) error {
	db.AddQueryHook(bundebug.NewQueryHook(bundebug.WithVerbose(true)))

	err := CreateSchema(ctx, appState, db)
	if err != nil {
		return fmt.Errorf("failed to create schema: %w", err)
	}

	db.RegisterModel((*UserSchema)(nil), (*SessionSchema)(nil), (*DocumentCollectionSchema)(nil))

	fixture := dbfixture.New(db, dbfixture.WithRecreateTables())

	files, err := os.ReadDir(fixturePath)
	if err != nil {
		return fmt.Errorf("failed to read directory: %w", err)
	}

	for _, file := range files {
		if !file.IsDir() {
			switch filepath.Ext(file.Name()) {
			case ".yaml", ".yml":
				err := fixture.Load(ctx, os.DirFS(fixturePath), file.Name())
				if err != nil {
					return fmt.Errorf("failed to load fixture %s: %w", file.Name(), err)
				}
			}
		}
	}

	err = createTestDocumentTables(ctx, db)
	if err != nil {
		return fmt.Errorf("failed to create test document tables: %w", err)
	}

	return nil
}
