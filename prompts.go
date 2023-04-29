package main

const progressivePromptTemplate = `
Progressively summarize the lines of conversation provided, adding onto the previous summary returning a new summary. If the lines are meaningless just return NONE

EXAMPLE
Current summary:
The human asks who is the lead singer of Motörhead. The AI responds Lemmy Kilmister.
New lines of conversation:
Human: What are the other members of Motörhead?
AI: The original members included Lemmy Kilmister (vocals, bass), Larry Wallis (guitar), and Lucas Fox (drums), with notable members throughout the years including "Fast" Eddie Clarke (guitar), Phil "Philthy Animal" Taylor (drums), and Mikkey Dee (drums).
New summary:
The human asks who is the lead singer and other members of Motörhead. The AI responds Lemmy Kilmister is the lead singer and other original members include Larry Wallis, and Lucas Fox, with notable past members including "Fast" Eddie Clarke, Phil "Philthy Animal" Taylor, and Mikkey Dee.
END OF EXAMPLE

Current summary:
{{.PrevSummary}}
New lines of conversation:
{{.MessagesJoined}}
New summary:
`

type ProgressivePromptTemplateData struct {
	PrevSummary    string
	MessagesJoined string
}
