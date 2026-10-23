# Vibecoded design: master avoid list

Compiled from web sources in October 2026. Items marked **Claude** are attributed specifically to Claude, Claude Design or Opus output. The rest are general AI-builder tells that Claude also produces. Sources are listed at the bottom.

Most sources agree that one item alone proves nothing. Four or more on one page is the recognizable "AI look."

## Color

- **Claude:** cream or parchment background, near-black ink and one rust or terracotta accent. Generative Labs sampled #F7F1E4, #211D18 and #D9622B. taste-skill calls this the "assistant palette."
- **Claude:** swapping cream for another fixed palette, such as mint with forest green, near-black with gold, or beige with brass. Anthropic's guidance says "don't use cream" just shifts the model to a different fixed palette.
- **Claude:** a teal accent on every button, headline accent, focus ring and chart. Default Claude decks use teal and coral.
- Purple, indigo or violet gradients, especially on white. This includes "VibeCode purple" and Tailwind indigo-500.
- Dark mode by default, with neon or glowing accents and cyan on dark.
- Gradient text on headings or numbers.
- Gradient backgrounds and blobs, mesh gradients, and radial halos or spotlight glows behind content.
- Large colored glows and colored shadows.
- Gray text on colored backgrounds, and gray body text that barely passes contrast on dark.
- Pure #000 or #fff.
- The slate or zinc neutral palette that ships with shadcn/ui.

## Typography

- Inter, plus Roboto, Arial, Open Sans, Lato and system-ui.
- **Claude:** Space Grotesk. Anthropic says Claude converges on it across generations.
- Geist, Instrument Serif and Fraunces, which keep appearing together.
- **Claude:** big italic serif headlines, or one serif-italic accent word inside a sans headline.
- **Claude:** a Tiempos- or Source Serif-style headline over a sans body, which reads as leftover Anthropic branding.
- One colored, italic or underlined accent word in a headline.
- Eyebrows or kickers, meaning a tiny uppercase letter-spaced label above a heading.
- A pill badge or "New ·" chip above the main headline.
- Monospace used to look technical, such as JetBrains Mono outside real code or data.
- All-caps headings, labels or body text.
- Headlines longer than two lines, or a hero headline that fills the whole screen.
- A flat hierarchy: one font in three weights, with the defaults of 16px, weight 400 and 1.5 line height everywhere.
- More than two typefaces.

## Layout and components

- A centered hero with a headline, a subheadline that restates it, a gradient or blob background and one centered button.
- Rows of identical feature cards, each with an icon on top, a bold word and one sentence, especially three across.
- Icons sitting in tinted rounded squares or colored circles.
- Cards everywhere, and cards nested inside cards ("container soup").
- A colored accent stripe on the left or top edge of cards. Developers Digest calls the left-edge version "the most specific tell."
- A 1px border, rounded-2xl corners and a soft grey shadow on every card, or a thin border paired with a wide shadow.
- The hero metric template: a big number, a small label, a row of supporting stats and a gradient accent. Stat banner rows fall here too.
- Numbered section labels such as "01 / 02 / 03", "(001)" or "Step 2", and rows of 1-2-3 steps.
- **Claude:** the "newspaper" preset, with hairline rules, zero radius, dense columns, tracked capitals, middle-dot meta strings and monospace data labels.
- Hairline dividers, 1px outlined boxes, grid lines and crop marks everywhere.
- **Claude:** ticker bars "as if the page were a news channel," and auto-scrolling marquees.
- **Claude:** dashboards built from rounded rectangles, and fake dashboards of grey bars.
- Bento grids as the default feature layout.
- Glassmorphism, backdrop blur and floating pill navigation.
- Decorative grid-line backgrounds and repeating diagonal stripes.
- **Claude:** noise texture, vignette and scanline overlays.
- Abstract SVG blobs, icons or shapes standing in for real images.
- Pill-shaped buttons everywhere, or every button styled as primary.
- Everything centered, the same spacing everywhere, or unbalanced columns.
- Only Tailwind, shadcn/ui and Lucide icons, with nothing custom.

## Motion

- A fade-up on every section, or staggered reveals on everything.
- **Claude:** a pulsing or blinking "live" status dot.
- Blinking cursors and "AI is thinking" loops.
- Bounce or elastic easing.
- Content that stays invisible until an entrance animation runs.
- Animating width, height or padding instead of transform and opacity.

## Copy

- Em dashes in nearly every sentence.
- "Not X, but Y" constructions.
- Vague claims that could describe 100 other companies, and generic superlatives.
- Stock words such as seamless, elevate, unlock, empower and supercharge.
- Generic calls to action such as "Get started."
- A subheadline that repeats the headline.
- Invented proof: fake customers, quotes, logos or statistics.
- Emoji used as icons.

## Slide decks

- **Claude:** the default deck, with a dark teal title band and rounded cards in slightly different teals. Each card has an icon in a colored circle, everything sits on a soft drop shadow, and serif titles go over a sans body in teal and coral.
- **Claude:** the Claude Design sales-deck skeleton: a bright opening slide, a mission statement in three bullets, a market slide made of four rectangles and a single "our move" line.
- **Claude (Opus 5.5):** a large empty band under the slide body, which makes slides look unfinished.
- **Claude:** ignoring the brand by replacing its color with dark purple and adding its own background art.
- Charts drawn as plain boxes instead of real charts.
- The same tone and template on every slide, in the generic slide order.

## Sources

- Anthropic, [Improving frontend design through Skills](https://claude.com/resources/articles/improving-frontend-design-through-skills)
- Generative Labs, [Why Do Claude Designs All Look the Same?](https://www.generativelabs.com/insights/why-ai-design-tools-look-the-same)
- Muzli, [Opus 5.5 builds Muzli Picks-level websites](https://muz.li/blog/claude-opus-5-5-for-designers/)
- Impeccable, [Slop rule list](https://impeccable.style/slop) and [anti-patterns](https://impeccable.style/anti-patterns/), plus the [docs mirror](https://mintlify.wiki/pbakaus/impeccable/concepts/anti-patterns)
- taste-skill, [SKILL.md house rules](https://github.com/Leonxlnx/taste-skill/blob/main/skills/taste-skill/SKILL.md)
- Hallmark, [README gates](https://github.com/nutlope/hallmark)
- awesome-claude-design, [community takes and fingerprints](https://github.com/rohitg00/awesome-claude-design)
- Developers Digest, [AI Design Slop: 16 Patterns](https://www.developersdigest.tech/blog/ai-design-slop-and-how-to-spot-it)
- DEV Community, [Why every Claude Code-built site looks the same](https://dev.to/kkk_dev_1b0a00f5047cb4de6/why-every-claude-code-built-site-looks-the-same-and-the-image-layer-that-breaks-it-37jp)
- AI Skill Market, [Banning Inter: Why Font Defaults Are the Slop Tell](https://aiskill.market/blog/banning-inter-the-font-tell)
- You Can Build Things, [Claude Design Generic Output](https://youcanbuildthings.com/articles/claude-design-generic-output/)
- Matt Segar, [Teaching Claude to Make Slides That Look Like Mine](https://segar.me/blog/posts/teaching-claude-my-slide-style.html)
- Slideworks, [Specialized AI slide generators vs ChatGPT and Claude](https://slideworks.io/resources/specialized-ai-slide-generator-vs-chatgpt-claude)
- MindStudio, [How to Make Your AI-Generated Website Not Look Vibecoded](https://www.mindstudio.ai/blog/avoid-vibecoded-landing-pages)
- Bare Metal Digest, [The Design Gap Between Claude Web and Claude Code](https://baremetaldigest.substack.com/p/the-design-gap-between-claude-web) (via search summary; the page did not load)
