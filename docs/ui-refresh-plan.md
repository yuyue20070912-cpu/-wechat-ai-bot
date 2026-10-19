# fishyeah Chat UI refresh — implementation plan

Status: design branch only; no production deployment.
Branch: `design/fishyeah-ui-refresh-v1` (created from `main`).

## Product constraints
- Preserve all login, quotas, subscriptions, payments, chat streaming, and learning routes.
- Keep AI speaking feedback, transcription and associated paid AI calls disabled.
- Do not alter database schema, production environment variables, deployment settings or secrets.
- Keep existing fishyeah Chat identity, icon and chat avatar.

## UI direction
- Premium, quiet black/white neutral palette with restrained accent colors.
- Mobile-first PWA. The conversation is the central surface; secondary controls live in a compact left drawer.
- Show the user's phone number only inside the account/menu view.
- No avatar for outgoing user bubbles; keep existing green outgoing bubble preference. Incoming assistant messages use the fishyeah Chat logo.
- Theme control must not overlap other icons.
- Consistent typography, spacing, corner radii, elevation and focus styles.

## Work phases
1. **Inventory and baseline**: identify templates, static CSS/JS, shared components, existing tests, screenshot baseline at iPhone and desktop widths.
2. **Design tokens**: CSS variables for spacing, typography, colors, borders, radii and light/dark modes; respect reduced motion and contrast.
3. **Chat shell**: responsive header, drawer, chat transcript, streaming states, fixed composer, safe-area insets and keyboard behavior.
4. **Learning and account surfaces**: align IELTS/CET-4/CET-6, plans, reviews, auth and membership without changing APIs.
5. **Quality gates**: test mobile Safari PWA, desktop Safari/Chrome, login, streaming, quota, subscription/payment, learning exercises, navigation, touch targets and accessibility. WCAG 2.2 AA target minimum is 24x24 CSS pixels with exceptions; design for larger targets when practical.

## Release rules
- Make implementation commits only on this branch.
- Open a **Draft PR** to main for review.
- Do not merge or trigger Render deployment without explicit approval.
- No destructive database changes; no AI speaking feedback rollout.

## Acceptance criteria
- At 375px mobile width, no horizontal scrolling or clipped controls.
- Chat composer remains usable when iPhone keyboard is visible.
- Light/dark modes work without obstructing navigation.
- Main chat remains visually dominant; secondary options are reachable by drawer.
- Existing user-facing flows pass regression checks.
