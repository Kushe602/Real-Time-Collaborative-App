# CollabSpace UI guide

## Design

The shared template adds a modern dark presentation, soft indigo/cyan ambient gradients, consistent focus indicators, and reduced-motion handling. Existing static CSS, Tailwind, HTMX, and Jinja blocks are retained.

## Landing page

The landing page now introduces the workspace concept, summarizes Kanban, shared documents, whiteboard, chat, and presence, and provides clear calls to action. Its workspace panel is an illustrative preview, not a live workspace connection.

## Existing interactions preserved

- Authenticated users continue to use `/dashboard`.
- Guests continue to use `/login` and `/register`.
- The feature section remains reachable through `#features`.
- Existing workspace templates and static app stylesheets remain in place.

## Verification checklist

1. Run the local setup from the root README.
2. Check landing, login, registration, dashboard, and workspace screens.
3. Use two sessions to verify board edits, document updates, whiteboard actions, chat, presence, and notifications.
4. Check keyboard focus and mobile-width layouts.
5. Run the existing test suite and CI workflow.

This change has not been runtime- or browser-tested as part of the update.
