package extractors

import (
	"github.com/danielchalef/zep/pkg/models"
	"github.com/spf13/viper"
)

func Initialize(appState *models.AppState) {
	log.Info("Initializing extractors")

	if viper.GetBool("extractors.summarizer.enabled") {
		extractor := &SummaryExtractor{}
		appState.MemoryStore.Attach(extractor)
		log.Info("SummaryExtractor attached to memory store")
	}
	if viper.GetBool("extractors.embeddings.enabled") {
		extractor := &EmbeddingExtractor{}
		appState.MemoryStore.Attach(extractor)
		log.Info("EmbeddingExtractor attached to memory store")
	}
}
