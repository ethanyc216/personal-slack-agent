# Slack Client Findings

This note captures durable findings from inspecting the Slack web client while testing `personal_slack_agent`.

## Scope

- Source of truth was a live Slack web session in Chrome, attached through CDP.
- Traffic inspected was Slack's private web client API under `/api/...`, not the public Slack app OAuth flow.
- Sensitive workspace-specific values such as tokens are intentionally omitted from this document.

## Fresh Client Load

When opening a route like `https://app.slack.com/client/<team>/<channel>`, Slack does all of the following:

- Opens a websocket to `wss://wss-primary.slack.com/...` using the Slack web session token.
- Sends a burst of private API requests to `https://<workspace>.slack.com/api/...`.
- Uses the websocket for live change detection and the private API for hydration and detail fetches.

Observed high-value route bootstrap calls:

- `conversations.view`
  - Includes the current `channel` id in the request body.
  - Returns the current channel object, including channel id and channel name.
  - Also returns initial channel history inline.
- `conversations.history`
  - Fetches visible message history for the current channel.
- `client.counts`
  - Returns many channel ids with read or unread metadata.
  - Useful for known-id tracking, but not sufficient by itself for name-to-id resolution.
- `client.userBoot`
  - Returns broad workspace bootstrap state.
  - May include a partial `channels` object list and a larger `channels_priority` map keyed by channel id.
  - In testing, this was not a reliable full name-to-id directory for all sidebar-visible channels.

Other calls observed during bootstrap included:

- `experiments.getByUser`
- `api.features`
- `features.access.policies.list`
- `client.shouldReload`
- `enterprise.prefs.get`
- `users.prefs.get`
- `client.appCommands`
- `client.extras`
- `drafts.list`
- `drafts.listActive`
- `users.channelSections.list`
- `conversations.listPrefs`
- `conversations.historyChanges`
- `bookmarks.list`
- `conversations.bulkReacjiTriggers`
- `conversations.genericInfo`

## Sidebar Click Behavior

Clicking a rendered sidebar channel item showed that Slack already has the channel id in the DOM before the click.

Observed DOM pattern:

- Name node:
  - `span[data-qa="channel_sidebar_name_<channel-name>"]`
- Parent channel node:
  - `div[data-qa="channel-sidebar-channel"]`
  - `data-qa-channel-sidebar-channel-id="<channel-id>"`
  - dataset field `qaChannelSidebarChannelId`
- Virtual list container node:
  - dataset field `itemKey="<channel-id>"`

Important detail:

- There was no anchor `href` on the channel name node or its nearby ancestors.
- The channel id was available directly on the sidebar item container via DOM attributes.

This means:

- If a target channel is already rendered in the sidebar, the fastest no-navigation resolution path is DOM lookup, not API lookup.
- A robust selector strategy is:
  1. Find `data-qa="channel_sidebar_name_<normalized-name>"`.
  2. Read the closest ancestor with `data-qa-channel-sidebar-channel-id`.
  3. Use that id to build the canonical route `https://app.slack.com/client/<team>/<channel-id>`.

## What Happens After Click

Clicking a sidebar channel item caused the page URL to switch from:

- `/client/<team>/<old-channel-id>`

to:

- `/client/<team>/<new-channel-id>`

Immediately after click, Slack issued only a small hydration fan-out. The main calls observed were:

- `conversations.history`
- `conversations.listPrefs`
- `bookmarks.list`

This suggests the route transition is mostly client-side. Slack does not need a separate "resolve channel name to id" API call at click time when the sidebar item is already rendered.

## Thread Behavior

On plain channel load, Slack did not call `conversations.replies`.

Implication:

- Thread replies are fetched lazily when the thread pane is opened, not as part of ordinary channel bootstrap.

## Practical Guidance For Bob

Preferred channel-id resolution order:

1. Use the configured workspace `slack_url` to attach to the correct Slack workspace.
2. If the channel is rendered in the Slack sidebar, resolve the id from the DOM using `data-qa-channel-sidebar-channel-id`.
3. If needed, click the sidebar item and confirm the resulting route `/client/<team>/<channel-id>`.
4. Treat broad bootstrap APIs such as `client.userBoot` and `client.counts` as supporting signals, not the sole name-to-id resolver.

Practical API guidance:

- Use `conversations.view` as the clearest current-route source of truth.
- Use `conversations.history` and `conversations.replies` for message retrieval.
- Use `chat.postMessage` for sending Bob messages.
- Expect websocket plus API together; websocket alone is not enough for full hydration.

## Current Limits

- A fresh client load did not reveal a complete, reliable, no-click API-only map of every sidebar channel name to channel id.
- If a channel is not rendered in the current sidebar DOM, additional investigation is needed to determine whether Slack exposes it through a lazy API or only after UI interaction.
