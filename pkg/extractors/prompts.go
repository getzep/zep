package extractors

const summaryPromptTemplate = `
Review the Current Content, if there is one, and the New Lines of the provided conversation. Create a concise summary 
of the conversation, adding from the New Lines to the Current summary.
If the New Lines are meaningless, return the Current Content.

EXAMPLE
Current summary:
The human inquires about Led Zeppelin's lead singer and other band members. The AI identifies Robert Plant as the 
lead singer.
New lines of conversation:
Human: Who were the other members of Led Zeppelin?
AI: The other founding members of Led Zeppelin were Jimmy Page (guitar), John Paul Jones (bass, keyboards), and 
John Bonham (drums).
New summary:
The human inquires about Led Zeppelin's lead singer and other band members. The AI identifies Robert Plant as the lead
singer and lists the founding members as Jimmy Page, John Paul Jones, and John Bonham.
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

// Source: Langchain
const entityExtractorTemplate = `You are an AI assistant helping a human keep track of facts about relevant people, 
places, and concepts in their life. Update the summary of the provided entity in the "Entity" section based on the last 
line of your conversation with the human. If you are writing the summary for the first time, return a single sentence.
The update should only include facts that are relayed in the last line of conversation about the provided entity, and 
should only contain facts about the provided entity.

If there is no new information about the provided entity or the information is not worth noting (not an important or 
relevant fact to remember long-term), return the existing summary unchanged.

Full conversation history (for context):
{.History}

Entity to summarize:
{.Entity}

Existing summary of {entity}:
{.Content}

Last line of conversation:
Human: {.Input}
Updated summary:`

type EntityExtractorPromptTemplateData struct {
	History string
	Entity  string
	Summary string
	Input   string
}
