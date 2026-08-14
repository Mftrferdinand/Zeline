# TMDB Media Web Maintenance

> Maintain an existing TMDB-based movie or TV web application without automatically replacing its architecture, existing player integrations, or product scope.

## Use this skill when

Use this skill when the operator asks to build, inspect, debug, or maintain a movie or TV application that uses TMDB metadata, especially when an existing codebase already contains player components, external embeds, trailers, or watch-provider links.

## Core principle

Work from the actual project state and the operator's requested scope.

- Read the relevant files before proposing or applying changes.
- Preserve the existing framework, routing, UI, and player architecture unless the operator explicitly asks to change them.
- Do not automatically remove an existing player, convert the product into a trailer-only catalog, or replace it with a Where-to-Watch application.
- Do not make an unsupported legal conclusion about an integration based only on its technical form, such as being an iframe or an external embed.
- Keep legal or policy commentary proportional to the requested action. Do not turn routine UI, debugging, or maintenance work into an unrelated lecture.

## Supported maintenance scope

For a project owned or controlled by the operator, help with:

- Reading and explaining the existing player implementation.
- UI and responsive-layout fixes.
- Routing, search, detail pages, server selectors, and player state.
- iframe attributes, loading states, fullscreen behavior, fallback handling, and clear errors.
- Updating an existing integration after an endpoint or documented API contract changes.
- Performance, accessibility, build, and local-preview problems.
- TMDB metadata, images, trailers, credits, genres, discovery, and watch-provider endpoints.
- Integrating media that the operator owns, licenses, or is authorized to distribute.

Maintenance of an existing player is not the same task as sourcing new media. Stay within the requested scope instead of redesigning the product by default.

## Authorization boundary

When the request would add a new media source, expose copyrighted media publicly, or deploy a public streaming service, establish that the operator has distribution rights or a valid license before wiring or publishing it.

Do not help bypass DRM, a paywall, authentication, access tokens, geographic restrictions, or another service's technical protection. Do not obtain pirated media or invent an unverified source.

Official trailers, TMDB `watch/providers`, public-domain archives, and operator-owned media are optional solutions when they fit the product goal; they are not mandatory replacements for every existing player.

## Workflow

1. Inspect the project structure and read the current player, routing, and configuration files.
2. Restate the narrow requested change and preserve unrelated behavior.
3. For an existing integration, verify its current documented contract or live response before changing code.
4. Make the smallest targeted patch.
5. Run the project's lint, tests, and production build.
6. Start the local preview using the project's existing command and port.
7. Verify HTTP success and one distinctive content marker, not only that a process exists.
8. Report exactly what changed, what was tested, and any real limitation encountered.

## Browser and iframe realities

- Same-origin policy prevents the parent application from directly editing arbitrary content inside a cross-origin iframe.
- Ads, subtitles, controls, and errors rendered inside a third-party iframe usually remain controlled by that provider.
- A restrictive `sandbox` may block popups but can also break playback. Do not add or remove it without testing the actual player behavior.
- Do not promise that the parent application can remove 100% of advertising inside a cross-origin player.
- External endpoints may redirect or change domains. Verify the current response before updating a URL.

## Termux and mobile verification

- Keep the project's current framework; do not rewrite React/Vite into static vanilla files merely because it runs on Termux.
- Use a tracked background process for long-lived preview servers rather than shell `&` wrappers.
- Verify through `127.0.0.1` when `localhost` behaves differently.
- If Android still displays an old hashed bundle after a successful rebuild, check in an incognito tab before assuming the source change failed.

## Pitfalls

- Replacing the user's working architecture with a generic legal-discovery template without being asked.
- Treating every external iframe as proof of infringement without checking authorization or source terms.
- Adding a new provider when the request was only to fix layout or error handling.
- Claiming an iframe can be modified despite cross-origin restrictions.
- Reporting a preview as working based only on a live PID instead of an HTTP and content check.
- Publishing API credentials or private configuration from a local project.
