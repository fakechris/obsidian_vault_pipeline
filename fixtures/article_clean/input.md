---
title: "A Guide to Agent-native Product Management"
source: "https://every.to/guides/ai-product-management-guide?source=post_button"
author:
  - "[[Marcus Moretti]]"
published: 2026-04-27
created: 2026-05-02
description: "A step-by-step guide to using agentic capabilities for better product management"
tags:
  - "clippings"
---
## The discipline of product management

“Product management” was born in the 1930s within the consumer goods giant Procter & Gamble. As the company expanded its product offering, leaders realized their products would be more successful if they ceded control to direct managers of the products. Someone needed to be in charge of each product, and they called that person the “Brand Man.” The raison d’etre of product management—ownership and accountability—survives to this day.

In the intervening years, however, the product management job description has been rewritten several times over. In the 1940s and 1950s, Hewlett-Packard’s product managers became the middlemen between customers and engineers. Toward the end of the century, internet startup PMs added user experience design, agile development, and A/B testing to their toolkits.

Now, PMs need to be good at everything: design and diplomacy, sales and statistics. Thousands of startups have raised billions of dollars to help PMs across these disciplines. But that introduced a new problem: The average company today has over [100 software subscriptions](https://www.saasacademy.com/blog/saas-statistics%5C), an overload that impacts PMs more than other functions given how many other roles and disciplines they interact with. No wonder many people I know in product management feel burnt out.

Now, much of the interdisciplinary work that goes into product management can be done by an LLM in minutes, sometimes seconds. What used to be a three-hour-long analytics investigation is now a simple back-and-forth with Claude. A product review that used to be a fortnightly chore emerges from a single typo-ridden chat message.

This has been my recent experience, at least. I no longer struggle with semicolons in SQL queries or even write tickets. All of my product management work happens in conversation with, in my case, Claude Code. The conversation is the work.

The following guide is a point-in-time snapshot of how I’m doing product management with agents. New AI tools launch every day, and my workflow changes at least weekly. I’ve tried to capture here the main pillars of my workflow that likely won’t change for months. It’s hard to see ahead farther than that these days.

## The main PM loop

### Plan → ship → review → repeat

This is a familiar software development lifecycle (SDLC) loop. Product management happens mainly at the “plan” and “review” stages. For more on the “ship” stage, check out my colleague **[Kieran Klaassen](https://every.to/@kieran_1355)** ’s [guide to compound engineering](https://every.to/guides/compound-engineering).

**Plan**: This starts with a product strategy. What is the problem we’re solving, and how do we solve it? Who is our product for? How do we measure success? What are the main tracks of work to realize our solution? This strategy then informs feature ideas, prioritization, and feature specs.

**Ship**: Build the thing. Make sure it works. Deploy it.

**Review**: Gather the learnings from building the thing. Save those for later. Once the feature has been live for some time, check the metrics. This will be covered below in the new “product-pulse” skill that’s part of compound engineering. It’s important to pick the right metrics, measure them effectively, and regularly review them.

Everything that ships is an experiment. You never know for sure how users are going to react to something new. The more you ship, the more you learn—and those learnings reinforce themselves over time, allowing you to serve customers better. Once enough learnings accumulate, it’s time to revisit the strategy. Is this still the winning approach, in light of what we now know? The answer may be yes, but if it’s no, change it and get back to shipping.

But everything starts with…

## The strategy document

As Kieran says, software development has shifted from 20 percent planning and 80 percent execution to 80 percent planning and 20 percent execution. The foundation of all software planning is the strategy.

The new compound engineering command `/ce-strategy` takes its structure from the book *[Good Strategy Bad Strategy](https://www.youtube.com/watch?v=4uWKEG0s9Kc)* by management professor **Richard Rumelt**. As the title suggests, he surveys lots of real examples, from companies and governments, and classifies them as good strategies or bad strategies.

The first time you run `/ce-strategy` in an agent environment (Claude Code, Codex, etc.), you’ll be asked a series of questions and ultimately get a `strategy.md` file. The components of `strategy.md` are:

1. **Target problem.** What is the current pain that people feel, which will encourage them to entertain your pitch? Ideally, this is a recurring, expensive problem.
2. **Approach.** One or two sentences describing the guiding policy for the product. If you said these sentences to a person experiencing the target problem, they should be unmistakably intrigued. The approach is not a goal or a generic positive description (“better tools for X”), and it’s not a feature. The approach is a description of your product’s specific angle of solving the target problem.
3. **Who it’s for.** This section describes the one or more personas that experience the target problem and would be interested in your approach to solving it. Sometimes a product can have a wide range of personas who might be interested in it. Taking a page from the book *[Crossing the Chasm](https://en.wikipedia.org/wiki/Crossing_the_Chasm)* by management expert Geoffrey A. Moore, it’s best to focus early on a small number—ideally one—persona, and nail the product offering for them. You land the product with them, then expand to other segments.
4. **Key metrics.** Three to five. These should be S.M.A.R.T.: specific, measurable, actionable, relevant, and timely. For Spiral, one of our key metrics is drafts exported—it’s a clear sign that someone got value from the product. Avoid shallow metrics like page views, and avoid vanity metrics that look good but don’t translate to real value. Pick the metrics that undeniably show people are getting value. At a bare minimum, track people and dollars.
5. **Tracks.** Two to four core capabilities, each with a one-line description. Track one is usually core performance, sometimes called “platform.” The other tracks should be significant, multi-month initiatives that, when complete, should grow your meaningful metrics. “Integrations” would be a simple example of a track; “Slack integration” is too specific. More than four tracks usually means lack of focus.

There are two optional sections. “Not working on” is an explicit list of things that might be tempting but are not near-term priorities. It’s sometimes helpful to state this up front to head off distractions. “Marketing/positioning” is a list of things the product team will work on to support growth.

The strategy doc doesn’t contain product requirements. There are no specific features, issues, or statuses described in detail. It’s the big picture of the product. The specs come later.

### Filling it in

Writing a strategy document cold is hard. The best way to do it, I’ve found, is to have an agent interview you. The `ce-strategy` skill does this. It runs through the sections in order and has built-in guidance about what makes a good answer (and what kinds of answers to push back on). The output is `docs/strategy.md`. You can rerun the command at any time to revisit a specific section without rewriting the whole thing.

The interview is deliberately conversational. If the first answer to, “What’s the core problem this product solves” is vague, the agent drills down: “Whose situation specifically? What do they try today, and why doesn’t it work?” The guidance here is taken from personal experience and from the Rumelt book.

### Compounding the strategy

Assuming you’re using compound engineering and shipping at post-AI speeds, you should rerun the strategy interview every few months. The next time you do the interview, your agent will have weeks or months of context from planning features, shipping them, and reviewing the data. The agent’s questions will be sharper, the conversation tougher.

### Shipping

I used to spend a lot of time writing tickets. I prided myself on detailed acceptance criteria (given, when, then) that left no room for engineering uncertainty for how a feature should work.

Now I no longer write tickets. Once you have a strategy document including the work tracks, I’d recommend using the compound engineering ideate, brainstorm, and plan skills to come up with what to build.

You need an issue tracker, and it should be one that has an MCP (model context protocol) or other agent integration. I use GitHub Issues, but I’ve had great experiences with Linear in the past. Your agent should write tickets for you, move them around the board, and keep the statuses up to date. You no longer read or write tickets; you just talk about them with your agent.

For statuses, I use lists of now/next/later, which roughly correspond to this week, next week, and… some point in the future. I don’t do sprints, just [Kanban](https://www.atlassian.com/agile/kanban). There’s “In Progress,” and there’s “Done.” That is all you need.

## Product pulse

The product pulse is where strategy meets reality. The pulse command is my main window into how the product is actually being used, whether features are successful, and how healthy the system is. A pulse is generated on demand, and the collection of pulses is the product’s memory.

### Creating a product pulse

This assumes your product is instrumented and logs are being stored somewhere. If that’s not the case, I would recommend you stop reading this and go set that up. (Posthog has a [self-setup wizard](https://posthog.com/docs/ai-engineering/ai-wizard).)

Like the strategy command, the `/ce:product-pulse` command interviews you the first time you run it.

- The first few questions are about what metrics to pay attention to, beginning with the KPIs you defined in the strategy document.
- In addition to those, you should have basic indicators of system health (server response times, error rates, etc.) and important funnels (onboarding, payment flows, team invites, etc.).
- Then it helps you connect your data sources.
- Finally, it generates a first pulse for you to evaluate and give feedback on. To change it, just ask.

### What makes up a pulse

A good pulse report fits on a single page (about 30 to 40 lines of terminal output) and covers four things:

1. **Headlines.** A handful of bullet points summarizing the key data from the window. If a reader only reads the first three lines, they should know what matters most.
2. **Usage.** Primary engagement event count, value-realization event count, conversions, and—if strategy metrics are set up—the current value of each with a delta versus the prior window. If the product has quality scoring for AI sessions, those scores appear here.
3. **System performance.** Latency percentiles (p50, p95, p99) versus the prior window, and top five error signatures by count with a one-line explanation of each. This section is omitted if no tracing tool is configured—the pulse still works without it.
4. **Followups.** One to five things worth investigating next. Specific enough to act on. If nothing anomalous happened, this section is thin.

### Making it run

#### Data sources

A pulse pulls from up to four categories of tools in the product’s stack:

- **Product analytics** (PostHog, Mixpanel, Amplitude) for what users did.
- **Application tracing** (Datadog, Sentry, Logfire, Honeycomb) for how the system performed.
- **Payments** (Stripe, Paddle) for trial and conversion numbers.
- **A read-only database connection** for anything the analytics tool can’t see. (The interviewer is instructed to reject a database connection with write access; ideally, you connect to a read replica.)

A team that has only one of these can still run a useful pulse. The report skips sections when the requisite data source isn’t available. With the number of data sources, quality beats quantity.

#### Wiring up MCP connections

The fastest way to let an agent query these tools on every run is to connect them via MCP. If you’re running Claude Code, `/mcp` lists what’s already connected. Your agent’s MCP registry is an easy way to find connectors, or you can use Google to search for them.

If a tool has no MCP available, the pulse can still work. The agent just needs a credentialed path (like a CLI or API), but agents seem to like MCPs.

#### Feedback channels

Pulse covers the quantitative half of feedback—metrics, errors, performance. The qualitative side has to come from users directly. I like emailing with users, so I made the Spiral email address conspicuous in the product, and emails to it land in my work inbox. I also include a 15-minute call booking link in every marketing email that goes out to users. There is no substitute for talking to users. You will never cease to be surprised by what they say.

Platforms like Canny and Featurebase are also good ways to collect and organize feature requests and bug reports. They have MCPs, which can be another good input into Pulse.

#### Memory: Saved reports

Every pulse run saves a copy to `~/pulse-reports/` as a Markdown file. A single pulse answers, “What happened today?” A folder of pulses answers, “What happened this month?” “When did this trend start?” “Did that feature change anything?” Over time, the folder becomes the team’s working memory of the product.

#### Running on a cadence

Claude Code has a Routines feature, which allows you to schedule frequent tasks, so you can automate recurring pulse runs. I have it run every day at 8 a.m., so I start work with the freshest perspective on how the product is doing. I typically run `/ce:product-pulse` manually a few times over the rest of the day.

#### Reading it like a founder

The agent is instructed to assemble the report, read it from the perspective of a founder, annotate anomalies, and run follow-up queries where necessary. For example, if a certain endpoint yielded higher errors, it will dig into those errors: Were they from one user? Did they coincide with a reported third-party outage? On the agent’s second pass, unless everything is completely normal, it will add a section at the end that preemptively answers natural follow-up questions. In this way, the agent works as an analyst, not just by pulling the data but by evaluating and presenting it.

By default, there are no hard-coded thresholds above or below which the agent will flag metrics. The agent evaluates the report using common sense and by comparison to previous pulse numbers. For example, if response times are suddenly three times higher on average, it will flag that and likely investigate further. If you do have specific performance goals—say, average system response time—you can ask your agent to reference those in the relevant section.

## The plugin

The `ce-strategy` and `ce-product-pulse` skills described in this guide ship inside Every’s open-source [compound-engineering](https://github.com/EveryInc/compound-engineering-plugin) plugin, installable in Claude Code with:

Install

`/plugin marketplace add EveryInc/compound-engineering-plugin`

`/plugin install compound-engineering`

Contributions are welcome.

## Going further

#### How the PM skills fit into compound engineering

When `docs/strategy.md` is present, the other compound-engineering skills (`ce-ideate`, `ce-brainstorm`, `ce-plan`) read it as grounding for their own work. Strategy flows into feature conception, specs, and ultimately shipped code. The next pulse reads the result. Those planning skills should be run with reference to past pulse reports as well, in order to make better feature design and prioritization decisions.

#### What’s not included

- **Prioritization.** I have a custom `/prioritize` command that I run daily. Compound engineering has a few planning skills—plan, ideate, brainstorm—that address this to some degree.
- **Pulse comparison.** Diffing two pulse reports—or tracking a specific metric across a window of saved reports—would be useful and isn’t in this version. The raw material is there (plain Markdown, dated filenames); a future skill could aggregate data across reports.
- **Per-stack customization paths.** The skills ship with generalized interviews. If you use a tool that’s not named, such as a niche analytics provider or a custom internal system, you’ll edit the configuration by hand. That’s fine—the skills are designed to be readable and editable—but it’s worth knowing going in.

#### Product management is now about the interesting parts

LLMs have allowed our tools to catch up with the multifaceted duties of product managers. For me, product management has been reduced to the interesting parts: dreaming up features, thinking through designs, looking at interesting data, and talking to users. We all feel the economic imperative to embrace AI tools, but the better reason, I think, is to make work more fun.