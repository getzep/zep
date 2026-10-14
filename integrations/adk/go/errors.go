package zepadk

import "errors"

// errNoUserUUID reports that Zep created a user but returned no UUID. The
// application cannot address the user without it.
var errNoUserUUID = errors.New("zepadk: Zep returned no user UUID")

// errNoThreadUUID reports that Zep created a thread but returned no UUID. The
// application cannot address the thread without it.
var errNoThreadUUID = errors.New("zepadk: Zep returned no thread UUID")

// errUnsupportedScope reports that a caller or a model asked for a graph
// search scope that this package does not support.
var errUnsupportedScope = errors.New("zepadk: unsupported search scope")

// errUnsupportedReranker reports that a caller or a model asked for a graph
// search reranker that this package does not support.
var errUnsupportedReranker = errors.New("zepadk: unsupported search reranker")

// errUnsupportedFilter reports that the search filters have a key that the
// selected Zep v4 search scope cannot apply.
var errUnsupportedFilter = errors.New("zepadk: the search filters have a key that the search scope cannot apply")
