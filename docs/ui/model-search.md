# Model catalog (Hugging Face)

## Layout
- Dedicated catalog view with a two-pane search and detail layout
- **Left:** Search results, filters, and sort
- **Right:** Model detail, README, files, install state, and download

## Left pane
- Search Hugging Face by model name, author, or repository
- Filter by lab and sort by relevance, downloads, or recent activity
- List items show name, description, capability tags, age, and install state

## Search results

Results are live data from Hugging Face. Empty, rate-limited, or offline
responses are shown as an empty state rather than fabricated staff picks.

## Right pane (selected model)
- Path + copy, close X
- Stats: downloads, stars, last updated
- Tags: PARAMS, ARCH, DOMAIN, FORMAT
- Capabilities: Vision, Tool Use, Reasoning
- Download card: filename, size, and selected library
- **Download [size]** starts a managed transfer
- README section rendered with sanitized markdown
- File list and local install detection

## Actions

- Copy model id
- Open repository links
- Start a download into the configured default library
- Track progress in the global downloads tray and Models → **Downloading**
