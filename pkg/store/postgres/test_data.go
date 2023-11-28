package postgres

import (
	"context"
	"fmt"
	"math/rand"
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
	UserSchema | SessionSchema | DocumentCollectionSchema | MessageStoreSchema
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

type CustomRandSource struct {
	rand.Source
}

// Intn Override
func (s *CustomRandSource) Intn(n int) int { //nolint:revive
	// 98% prob to return 1 (true)
	if rand.Float32() < 0.98 { //nolint:gosec
		return 1
	}
	return 0
}

func GenerateFixtureData(fixtureCount int, outputDir string) {
	fakerGlobal := gofakeit.NewUnlocked(0)
	gofakeit.SetGlobalFaker(fakerGlobal)

	// Generate test data for UserSchema
	users := make([]UserSchema, fixtureCount)
	for i := 0; i < fixtureCount; i++ {
		dateCreated := generateTimeLastNDays(14)
		users[i] = UserSchema{
			UUID:      uuid.New(),
			CreatedAt: dateCreated,
			UpdatedAt: dateCreated,
			UserID:    strings.ToLower(gofakeit.Username()),
			Email:     gofakeit.Email(),
			FirstName: gofakeit.FirstName(),
			LastName:  gofakeit.LastName(),
		}
	}
	// Generate test data for SessionSchema
	var sessions []SessionSchema
	for i := 0; i < fixtureCount; i++ {
		sessionCount := gofakeit.Number(1, 5)
		for j := 0; j < sessionCount; j++ {
			dateCreated := generateTimeLastNDays(14)
			sessions = append(sessions, SessionSchema{
				UUID:      uuid.New(),
				SessionID: gofakeit.UUID(),
				CreatedAt: dateCreated,
				UpdatedAt: dateCreated,
				UserID:    &users[i].UserID,
				Metadata:  gofakeit.Map(),
			})
		}
	}

	// Generate test data for DocumentCollection
	// Override fixtureCount to 10 for DocumentCollectionSchema
	fixtureCountCollections := 10
	collections := make([]DocumentCollectionSchema, fixtureCountCollections)
	embeddingDimensions := []int{384, 768, 1536}

	for i := 0; i < fixtureCountCollections; i++ {
		gofakeit.ShuffleInts(embeddingDimensions)
		dateCreated := generateTimeLastNDays(14)
		collectionName := strings.ToLower(gofakeit.Color() + gofakeit.AchAccount())
		tableName := generateTestTableName(collectionName, embeddingDimensions[0])
		var indexType models.IndexType
		if i%2 == 0 {
			indexType = "ivfflat"
		} else {
			indexType = "hnsw"
		}

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
				IndexType:           indexType,
				ListCount:           gofakeit.Number(1, 100),
				ProbeCount:          gofakeit.Number(1, 100),
			},
		}
	}
	// Generate test data for MessageStoreSchema
	var messages []MessageStoreSchema
	roles := []string{"ai", "human"}

	for _, session := range sessions {
		messageCount := gofakeit.Number(5, 30)
		wordCount := gofakeit.Number(1, 200)
		// Start from the session's creation time and increment for each message
		dateCreated := generateTimeLastNDays(14)
		gofakeit.ShuffleStrings(roles)
		for j := 0; j < messageCount; j++ {
			dateCreated = dateCreated.Add(time.Second * time.Duration(gofakeit.Number(5, 120)))
			messages = append(messages, MessageStoreSchema{
				UUID:       uuid.New(),
				CreatedAt:  dateCreated,
				UpdatedAt:  dateCreated,
				SessionID:  session.SessionID,
				Role:       roles[j%2],
				Content:    gofakeit.Paragraph(1, 5, wordCount, "."),
				Metadata:   gofakeit.Map(),
				TokenCount: gofakeit.Number(200, 500),
			})
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

	messageFixture := Fixtures[MessageStoreSchema]{
		{
			Model: "MessageStoreSchema",
			Rows:  messages,
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

	// Create document table directory if it doesn't exist
	documentTablePath := filepath.Join(outputDir, "document_tables")
	if _, err := os.Stat(documentTablePath); os.IsNotExist(err) {
		err = os.Mkdir(documentTablePath, 0755)
		if err != nil {
			fmt.Printf("unable to create %s: %v", documentTablePath, err)
			return
		}
	}

	// Write fixtures to YAML files
	writeFixtureToYAML(userFixture, outputDir, "user_fixtures.yaml")
	writeFixtureToYAML(sessionFixture, outputDir, "session_fixtures.yaml")
	writeFixtureToYAML(messageFixture, outputDir, "message_fixtures.yaml")
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
	defer func(file *os.File) {
		err := file.Close()
		if err != nil {
			fmt.Printf("error: %v", err)
			return
		}
	}(file)

	_, err = file.Write(data)
	if err != nil {
		fmt.Printf("error: %v", err)
		return
	}

	fmt.Printf("Fixtures generated successfully in %s!\n", filename)
}

func createTestDocumentTables(ctx context.Context, appState *models.AppState, db *bun.DB) error {
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
		err = createDocumentTable(ctx, appState, db, table.TableName, table.EmbeddingDimensions)
		if err != nil {
			return fmt.Errorf("failed to create table %s: %w", table.TableName, err)
		}

		err = addTestDocuments(ctx, db, table.TableName)
		if err != nil {
			return fmt.Errorf("failed to add test documents to table %s: %w", table.TableName, err)
		}
	}

	return nil
}

func addTestDocuments(
	ctx context.Context,
	db *bun.DB,
	tableName string,
) error {
	source := &CustomRandSource{rand.NewSource(time.Now().UnixNano())}
	r := rand.New(source) // nolint:gosec
	// 90% prob to return 1 (true), 10% to return 0 (false)
	boolFaker := gofakeit.NewCustom(r)

	documentCount := gofakeit.Number(50, 100)

	documents := make([]models.DocumentBase, documentCount)

	isFullyEmbedded := boolFaker.Bool()

	for i := 0; i < documentCount; i++ {
		var embedded = true
		if !isFullyEmbedded {
			embedded = boolFaker.Bool()
		}
		document := models.DocumentBase{
			DocumentID: gofakeit.Adjective() + gofakeit.Color() + gofakeit.Animal(),
			Content:    gofakeit.Sentence(50),
			IsEmbedded: embedded,
		}
		documents[i] = document
	}

	// add documents to table
	_, err := db.NewInsert().
		Model(&documents).
		ModelTableExpr(tableName).
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to insert documents: %w", err)
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

	dropSchemaQuery := `DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO postgres;
GRANT ALL ON SCHEMA public TO public;`

	_, err := db.ExecContext(ctx, dropSchemaQuery)
	if err != nil {
		return fmt.Errorf("failed to drop schema: %w", err)
	}

	// Enable vector extension
	err = enablePgVectorExtension(ctx, db)
	if err != nil {
		return fmt.Errorf("failed to enable pg_vector extension: %w", err)
	}

	err = CreateSchema(ctx, appState, db)
	if err != nil {
		return fmt.Errorf("failed to create schema: %w", err)
	}

	db.RegisterModel(
		(*UserSchema)(nil),
		(*SessionSchema)(nil),
		(*DocumentCollectionSchema)(nil),
		(*MessageStoreSchema)(nil),
		(*MessageVectorStoreSchema)(nil),
		(*DocumentSchemaTemplate)(nil),
	)

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

	err = createTestDocumentTables(ctx, appState, db)
	if err != nil {
		return fmt.Errorf("failed to create test document tables: %w", err)
	}

	return nil
}
