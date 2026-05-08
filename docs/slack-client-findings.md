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
- `users.channelSections.list`
  - Returns the user's sidebar section model.
  - In a later fresh-load capture, this was the clearest source for sidebar section names and ordering.
  - The response included `channel_sections`, `count`, `cursor`, `entities`, `last_updated`, and `ok`.
  - Each section item included `channel_section_id`, `name`, `type`, `channel_ids_page`, `next_channel_section_id`, and `last_updated`.

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
- `conversations.listPrefs`
- `conversations.historyChanges`
- `bookmarks.list`
- `conversations.bulkReacjiTriggers`
- `conversations.genericInfo`

## Sidebar Section Bootstrap

On 2026-05-06, a fresh load of `https://app.slack.com/client/<team>/<channel>`
was captured with cache disabled and service-worker bypass enabled for the temporary inspection tab.
Sensitive tokens, cookies, and concrete channel/user ids were not recorded in the durable notes.

Observed request pattern:

- `client.userBoot`
  - Returned broad workspace boot state.
  - Observed structural counts:
    - `channels: list[200]`
    - `ims: list[38]`
    - `prefs: dict[676]`
  - Channel objects included ids, names, normalized names, membership-ish flags, sharing flags, topic, purpose, and member lists.
  - IM objects included id, user, `is_open`, `is_im`, and update metadata.
- `users.channelSections.list`
  - Returned `channel_sections: list[14]`.
  - Section types observed included:
    - `recent_apps`
    - `standard`
    - `direct_messages`
    - `slack_connect`
    - `stars`
    - `salesforce_records`
    - `channels`
    - `agents`
  - `channel_ids_page` was an object containing `channel_ids`, `count`, and sometimes `cursor`.
  - Several custom `standard` sections had non-empty channel-id pages; examples of observed page sizes were 3, 35, 46, 15, 126, 15, and 32.
  - The terms corresponding to user-visible sidebar sections such as Bob, Priority, Direct Messages, OHAI, and Tools appeared in this response.
- `client.counts`
  - Returned read/unread metadata.
  - Observed structural counts:
    - `channels: list[264]`
    - `ims: list[38]`
    - `mpims: list[0]`
  - Items included `id`, `latest`, `last_read`, `mention_count`, `has_unreads`, `history_invalid`, and `updated`.
- `conversations.view`
  - Returned the selected channel object plus initial visible history for the URL's channel id.
  - This appears to be route hydration, not broad sidebar discovery.
- `conversations.history`
  - Fetched the selected channel's visible message history.
- `conversations.listPrefs`
  - Fetched preferences for the selected channel id.
- `wss://wss-primary.slack.com/...`
  - The websocket URL included flags such as `lazy_channels=1` and `no_query_on_subscribe=1`.
  - This likely keeps sidebar and unread state live after boot.

Notably, this fresh-load capture did not use `users.conversations` to build the visible sidebar.

Bob now has raw, unused wrappers for future experiments with the most relevant private web-client endpoints:

- `SlackApiClient.client_user_boot()`
- `SlackApiClient.users_channel_sections_list()`
- `SlackApiClient.client_counts()`
- `SlackApiClient.conversations_view()`
- `SlackApiClient.conversations_list_prefs()`
- Matching passthrough methods on `PlaywrightSlackAdapter`

These wrappers are intentionally not wired into watcher runtime discovery, configured-channel watching, or ultimate-search logic yet.

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
