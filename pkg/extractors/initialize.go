package extractors

import (
	"github.com/danielchalef/zep/pkg/models"
	"github.com/spf13/viper"
)

func Initialize(appState *models.AppState) {
	log.Info("Initializing extractors")

	if viper.GetBool("extractors.summarizer") {
		extractor := &MaxMessageWindowSummaryExtractor{}
		appState.MemoryStore.Attach(extractor)
		log.Info("MaxMessageWindowSummaryExtractor attached to memory store")
	}

}
