# Dogfood

> Exploratory QA of web apps: find bugs, evidence, reports.

## Overview

This skill guides you through systematic exploratory QA testing of web applications using the browser toolset. You will navigate the application, interact with elements, capture evidence of issues, and produce a structured bug report.

## Prerequisites

- Browser toolset must be available (`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_vision`, `browser_console`, `browser_scroll`, `browser_back`, `browser_press`)
- A target URL and testing scope from the user

## Inputs

The user provides:
1. **Target URL** — the entry point for testing
2. **Scope** — what areas/features to focus on (or "full site" for comprehensive testing)
3. **Output directory** (optional) — where to save screenshots and the report (default: `./dogfood-output`)

## Workflow

Follow this 5-phase systematic workflow:

### Phase 1: Plan

1. Create the output directory structure:
   ```
   {output_dir}/
   ├── screenshots/       # Evidence screenshots
   └── report.md          # Final report (generated in Phase 5)
   ```
2. Identify the testing scope based on user input.
3. Build a rough sitemap by planning which pages and features to test:
   - Landing/home page
   - Navigation links (header, footer, sidebar)
   - Key user flows (sign up, login, search, checkout, etc.)
   - Forms and interactive elements
   - Edge cases (empty states, error pages, 404s)

### Phase 2: Explore

For each page or feature in your plan:

1. **Navigate** to the page:
   ```
   browser_navigate(url="https://example.com/page")
   ```

2. **Take a snapshot** to understand the DOM structure:
   ```
   browser_snapshot()
   ```

3. **Check the console** for JavaScript errors:
   ```
   browser_console(clear=true)
   ```
   Do this after every navigation and after every significant interaction. Silent JS errors are high-value findings.

4. **Take an annotated screenshot** to visually assess the page and identify interactive elements:
   ```
   browser_vision(question="Describe the page layout, identify any visual issues, broken elements, or accessibility concerns", annotate=true)
   ```
   The `annotate=true` flag overlays numbered `[N]` labels on interactive elements. Each `[N]` maps to ref `@eN` for subsequent browser commands.

5. **Test interactive elements** systematically:
   - Click buttons and links: `browser_click(ref="@eN")`
   - Fill forms: `browser_type(ref="@eN", text="test input")`
   - Test keyboard navigation: `browser_press(key="Tab")`, `browser_press(key="Enter")`
   - Scroll through content: `browser_scroll(direction="down")`
   - Test form validation with invalid inputs
   - Test empty submissions

6. **After each interaction**, check for:
   - Console errors: `browser_console()`
   - Visual changes: `browser_vision(question="What changed after the interaction?")`
   - Expected vs actual behavior

### Phase 3: Collect Evidence

For every issue found:

1. **Take a screenshot** showing the issue:
   ```
   browser_vision(question="Capture and describe the issue visible on this page", annotate=false)
   ```
   Save the `screenshot_path` from the response — you will reference it in the report.

2. **Record the details**:
   - URL where the issue occurs
   - Steps to reproduce
   - Expected behavior
   - Actual behavior
   - Console errors (if any)
   - Screenshot path

3. **Classify the issue** using the issue taxonomy (see `references/issue-taxonomy.md`):
   - Severity: Critical / High / Medium / Low
   - Category: Functional / Visual / Accessibility / Console / UX / Content

### Phase 4: Categorize

1. Review all collected issues.
2. De-duplicate — merge issues that are the same bug manifesting in different places.
3. Assign final severity and category to each issue.
4. Sort by severity (Critical first, then High, Medium, Low).
5. Count issues by severity and category for the executive summary.

### Phase 5: Report

Generate the final report using the template at `templates/dogfood-report-template.md`.

The report must include:
1. **Executive summary** with total issue count, breakdown by severity, and testing scope
2. **Per-issue sections** with:
   - Issue number and title
   - Severity and category badges
   - URL where observed
   - Description of the issue
   - Steps to reproduce
   - Expected vs actual behavior
   - Screenshot references (use `MEDIA:<screenshot_path>` for inline images)
   - Console errors if relevant
3. **Summary table** of all issues
4. **Testing notes** — what was tested, what was not, any blockers

Save the report to `{output_dir}/report.md`.

## Tools Reference

| Tool | Purpose |
|------|---------|
| `browser_navigate` | Go to a URL |
| `browser_snapshot` | Get DOM text snapshot (accessibility tree) |
| `browser_click` | Click an element by ref (`@eN`) or text |
| `browser_type` | Type into an input field |
| `browser_scroll` | Scroll up/down on the page |
| `browser_back` | Go back in browser history |
| `browser_press` | Press a keyboard key |
| `browser_vision` | Screenshot + AI analysis; use `annotate=true` for element labels |
| `browser_console` | Get JS console output and errors |

## Tips

- **Always check `browser_console()` after navigating and after significant interactions.** Silent JS errors are among the most valuable findings.
- **Use `annotate=true` with `browser_vision`** when you need to reason about interactive element positions or when the snapshot refs are unclear.
- **Test with both valid and invalid inputs** — form validation bugs are common.
- **Scroll through long pages** — content below the fold may have rendering issues.
- **Test navigation flows** — click through multi-step processes end-to-end.
- **Check responsive behavior** by noting any layout issues visible in screenshots.
- **Don't forget edge cases**: empty states, very long text, special characters, rapid clicking.
- When reporting screenshots to the user, include `MEDIA:<screenshot_path>` so they can see the evidence inline.


---

## Lampiran: `references/issue-taxonomy.md`

# Issue Taxonomy

Use this taxonomy to classify issues found during dogfood QA testing.

## Severity Levels

### Critical
The issue makes a core feature completely unusable or causes data loss.

**Examples:**
- Application crashes or shows a blank white page
- Form submission silently loses user data
- Authentication is completely broken (can't log in at all)
- Payment flow fails and charges the user without completing the order
- Security vulnerability (e.g., XSS, exposed credentials in console)

### High
The issue significantly impairs functionality but a workaround may exist.

**Examples:**
- A key button does nothing when clicked (but refreshing fixes it)
- Search returns no results for valid queries
- Form validation rejects valid input
- Page loads but critical content is missing or garbled
- Navigation link leads to a 404 or wrong page
- Uncaught JavaScript exceptions in the console on core pages

### Medium
The issue is noticeable and affects user experience but doesn't block core functionality.

**Examples:**
- Layout is misaligned or overlapping on certain screen sections
- Images fail to load (broken image icons)
- Slow performance (visible loading delays > 3 seconds)
- Form field lacks proper validation feedback (no error message on bad input)
- Console warnings that suggest deprecated or misconfigured features
- Inconsistent styling between similar pages

### Low
Minor polish issues that don't affect functionality.

**Examples:**
- Typos or grammatical errors in text content
- Minor spacing or alignment inconsistencies
- Placeholder text left in production ("Lorem ipsum")
- Favicon missing
- Console info/debug messages that shouldn't be in production
- Subtle color contrast issues that don't fail WCAG requirements

## Categories

### Functional
Issues where features don't work as expected.

- Buttons/links that don't respond
- Forms that don't submit or submit incorrectly
- Broken user flows (can't complete a multi-step process)
- Incorrect data displayed
- Features that work partially

### Visual
Issues with the visual presentation of the page.

- Layout problems (overlapping elements, broken grids)
- Broken images or missing media
- Styling inconsistencies
- Responsive design failures
- Z-index issues (elements hidden behind others)
- Text overflow or truncation

### Accessibility
Issues that prevent or hinder access for users with disabilities.

- Missing alt text on meaningful images
- Poor color contrast (fails WCAG AA)
- Elements not reachable via keyboard navigation
- Missing form labels or ARIA attributes
- Focus indicators missing or unclear
- Screen reader incompatible content

### Console
Issues detected through JavaScript console output.

- Uncaught exceptions and unhandled promise rejections
- Failed network requests (4xx, 5xx errors in console)
- Deprecation warnings
- CORS errors
- Mixed content warnings (HTTP resources on HTTPS page)
- Excessive console.log output left from development

### UX (User Experience)
Issues where functionality works but the experience is poor.

- Confusing navigation or information architecture
- Missing loading indicators (user doesn't know something is happening)
- No feedback after user actions (e.g., button click with no visible result)
- Inconsistent interaction patterns
- Missing confirmation dialogs for destructive actions
- Poor error messages that don't help the user recover

### Content
Issues with the text, media, or information on the page.

- Typos and grammatical errors
- Placeholder/dummy content in production
- Outdated information
- Missing content (empty sections)
- Broken or dead links to external resources
- Incorrect or misleading labels



---

## Lampiran: `templates/dogfood-report-template.md`

# Dogfood QA Report

**Target:** {target_url}
**Date:** {date}
**Scope:** {scope_description}
**Tester:** Zeline (automated exploratory QA)

---

## Executive Summary

| Severity | Count |
|----------|-------|
| 🔴 Critical | {critical_count} |
| 🟠 High | {high_count} |
| 🟡 Medium | {medium_count} |
| 🔵 Low | {low_count} |
| **Total** | **{total_count}** |

**Overall Assessment:** {one_sentence_assessment}

---

## Issues

<!-- Repeat this section for each issue found, sorted by severity (Critical first) -->

### Issue #{issue_number}: {issue_title}

| Field | Value |
|-------|-------|
| **Severity** | {severity} |
| **Category** | {category} |
| **URL** | {url_where_found} |

**Description:**
{detailed_description_of_the_issue}

**Steps to Reproduce:**
1. {step_1}
2. {step_2}
3. {step_3}

**Expected Behavior:**
{what_should_happen}

**Actual Behavior:**
{what_actually_happens}

**Screenshot:**
MEDIA:{screenshot_path}

**Console Errors** (if applicable):
```
{console_error_output}
```

---

<!-- End of per-issue section -->

## Issues Summary Table

| # | Title | Severity | Category | URL |
|---|-------|----------|----------|-----|
| {n} | {title} | {severity} | {category} | {url} |

## Testing Coverage

### Pages Tested
- {list_of_pages_visited}

### Features Tested
- {list_of_features_exercised}

### Not Tested / Out of Scope
- {areas_not_covered_and_why}

### Blockers
- {any_issues_that_prevented_testing_certain_areas}

---

## Notes

{any_additional_observations_or_recommendations}
