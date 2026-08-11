# Figma MCP

> Fetch design context, screenshots, variables, and assets from Figma via the Figma MCP server, and translate Figma nodes into production code.

Use this skill when a task involves Figma URLs, node IDs, design-to-code implementation, or Figma MCP setup. It assumes a Figma MCP server is connected (see your MCP configuration).

## Required flow (do not skip)

1. Run `get_design_context` first to fetch the structured representation for the exact node(s).
2. If the response is too large or truncated, run `get_metadata` for the high-level node map, then re-fetch only the required node(s) with `get_design_context`.
3. Run `get_screenshot` for a visual reference of the node variant being implemented.
4. Only after you have both `get_design_context` and `get_screenshot`, download any needed assets and start implementation.
5. Translate the output (usually React + Tailwind) into the project's conventions, styles, and framework. Reuse the project's color tokens, components, and typography wherever possible.
6. Validate against Figma for 1:1 look and behavior before marking complete.

## Implementation rules

- Treat the Figma MCP output (React + Tailwind) as a representation of design and behavior, not final code style.
- Replace Tailwind utility classes with the project's preferred utilities/design-system tokens when applicable.
- Reuse existing components (buttons, inputs, typography, icon wrappers) instead of duplicating functionality.
- Use the project's color system, typography scale, and spacing tokens consistently.
- Respect existing routing, state management, and data-fetch patterns already in the repo.
- Strive for 1:1 visual parity. When conflicts arise, prefer design-system tokens and adjust spacing/sizes minimally to match visuals.
- Validate the final UI against the Figma screenshot for both look and behavior.

## Asset handling

- The Figma MCP server exposes an assets endpoint that serves image and SVG assets.
- If the server returns a `localhost` source for an image or SVG, use that source directly.
- Do NOT import/add new icon packages — all assets should come from the Figma payload.
- Do NOT use or create placeholders when a localhost source is provided.

## Link-based prompting

- The server is link-based: copy the Figma frame/layer link and give that URL to the MCP client when asking for implementation help.
- The client cannot browse the URL but extracts the node ID from the link; always ensure the link points to the exact node/variant you want.

## Setup notes

- Configure the Figma MCP server (Dev Mode MCP or a compatible server) in your MCP settings, providing any required access token via environment variable.
- Verify the connection by calling `get_metadata` on a known node before relying on it in a build.
