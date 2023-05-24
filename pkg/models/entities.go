package models

type EntityMatch struct {
	Start int    `json:"start"`
	End   int    `json:"end"`
	Text  string `json:"text"`
}

type Entity struct {
	Name    string        `json:"name"`
	Label   string        `json:"label"`
	Matches []EntityMatch `json:"matches"`
}

type EntityRequestRecord struct {
	UUID     string `json:"uuid"`
	Text     string `json:"text"`
	Language string `json:"language"`
}

type EntityResponseRecord struct {
	UUID     string   `json:"uuid"`
	Entities []Entity `json:"entities"`
}

type EntityRequest struct {
	Texts []EntityRequestRecord `json:"texts"`
}

type EntityResponse struct {
	Texts []EntityResponseRecord `json:"texts"`
}
