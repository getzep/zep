package extractors

const summaryPromptTemplate = `
Review the Current Summary, if there is one, and the New Lines of the provided conversation. Create a concise summary of the conversation, adding from the New Lines to the Current summary.
If the New Lines are meaningless, return the Current Summary.

EXAMPLE
Current summary:
The human inquires about Led Zeppelin's lead singer and other band members. The AI identifies Robert Plant as the lead singer.
New lines of conversation:
Human: Who were the other members of Led Zeppelin?
AI: The other founding members of Led Zeppelin were Jimmy Page (guitar), John Paul Jones (bass, keyboards), and John Bonham (drums).
New summary:
The human inquires about Led Zeppelin's lead singer and other band members. The AI identifies Robert Plant as the lead singer and lists the founding members as Jimmy Page, John Paul Jones, and John Bonham.
EXAMPLE END

Current summary:
{{.PrevSummary}}
New lines of conversation:
{{.MessagesJoined}}
New summary:
`

type SummaryPromptTemplateData struct {
	PrevSummary    string
	MessagesJoined string
}
