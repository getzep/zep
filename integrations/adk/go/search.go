package zepadk

import (
	zep "github.com/getzep/zep-go/v3"
)

// searchScopeSupported reports whether scope is one this package knows how to
// map into textual results. Unsupported scopes are rejected loudly rather than
// silently returning nothing.
//
// Supported scopes: edges (facts), nodes (entity summaries), episodes (raw
// message/data content), observations (derived memories), thread_summaries
// (incremental thread summaries), and auto (a pre-materialized Context Block
// returned in res.Context).
func searchScopeSupported(scope zep.GraphSearchScope) bool {
	switch scope {
	case zep.GraphSearchScopeEdges,
		zep.GraphSearchScopeNodes,
		zep.GraphSearchScopeEpisodes,
		zep.GraphSearchScopeObservations,
		zep.GraphSearchScopeThreadSummaries,
		zep.GraphSearchScopeAuto:
		return true
	default:
		return false
	}
}

// mapSearchResults flattens a Zep graph search response into a slice of textual
// results appropriate for the requested scope. Earlier versions read only
// res.Edges, so any non-edge scope (auto / nodes / episodes / observations /
// thread_summaries) silently returned zero results; this maps every
// supported scope.
//
//   - edges            -> each edge's Fact
//   - nodes            -> "name: summary" (or just the name when there is no summary)
//   - episodes         -> each episode's Content
//   - observations     -> "name: summary" (or just the name when there is no summary)
//   - thread_summaries -> "name: summary" (or just the name when there is no summary)
//   - auto             -> the single pre-materialized Context Block (res.Context)
//
// Empty entries are skipped. A nil res yields a nil slice.
func mapSearchResults(scope zep.GraphSearchScope, res *zep.GraphSearchResults) []string {
	if res == nil {
		return nil
	}

	switch scope {
	case zep.GraphSearchScopeAuto:
		if res.Context != nil && *res.Context != "" {
			return []string{*res.Context}
		}
		return nil

	case zep.GraphSearchScopeNodes:
		var out []string
		for _, node := range res.Nodes {
			if node == nil {
				continue
			}
			if text := nodeText(node); text != "" {
				out = append(out, text)
			}
		}
		return out

	case zep.GraphSearchScopeEpisodes:
		var out []string
		for _, ep := range res.Episodes {
			if ep != nil && ep.Content != "" {
				out = append(out, ep.Content)
			}
		}
		return out

	case zep.GraphSearchScopeObservations:
		var out []string
		for _, obs := range res.Observations {
			if obs == nil {
				continue
			}
			if text := observationText(obs); text != "" {
				out = append(out, text)
			}
		}
		return out

	case zep.GraphSearchScopeThreadSummaries:
		var out []string
		for _, summary := range res.ThreadSummaries {
			if summary == nil {
				continue
			}
			if text := threadSummaryText(summary); text != "" {
				out = append(out, text)
			}
		}
		return out

	case zep.GraphSearchScopeEdges:
		fallthrough
	default:
		var out []string
		for _, edge := range res.Edges {
			if edge != nil && edge.Fact != "" {
				out = append(out, edge.Fact)
			}
		}
		return out
	}
}

// nodeText renders an entity node as "name: summary", falling back to just the
// name (or just the summary) when one half is absent.
func nodeText(node *zep.EntityNode) string {
	return nameSummaryText(node.Name, node.Summary)
}

// observationText renders a derived observation node as "name: summary",
// falling back to whichever half is present.
func observationText(obs *zep.DerivedNode) string {
	summary := ""
	if obs.Summary != nil {
		summary = *obs.Summary
	}
	return nameSummaryText(obs.Name, summary)
}

// threadSummaryText renders a thread-summary node as "name: summary",
// falling back to whichever half is present.
func threadSummaryText(node *zep.GraphitiSagaNode) string {
	summary := ""
	if node.Summary != nil {
		summary = *node.Summary
	}
	return nameSummaryText(node.Name, summary)
}

// nameSummaryText joins a name and summary as "name: summary", returning just
// the populated half when one is empty and "" when both are.
func nameSummaryText(name, summary string) string {
	switch {
	case name != "" && summary != "":
		return name + ": " + summary
	case name != "":
		return name
	default:
		return summary
	}
}
