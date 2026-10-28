# Three Feet

An experimental space-heater safety helper for the **Meta Global AI Developer Hackathon**. Three Feet aims to detect objects in a heater's clearance zone, explain the concern, attempt to turn the heater off, and report whether shutdown was independently confirmed.

**Status:** Project concept and plan. No detection, measurement, or shutdown functionality has been implemented or validated yet. This is a research prototype, not a fire-safety device; do not rely on it to prevent fires or replace manufacturer instructions, smoke alarms, or safe heater practices.

## The problem

Objects such as blankets, curtains, and boxes can end up too close to a space heater. NFPA estimates that space heaters **and heating stoves together** accounted for approximately 30% of reported U.S. home heating equipment fires and 73% of associated civilian deaths in 2020–2024. [NFPA heating safety](https://www.nfpa.org/education-and-research/home-fire-safety/heating?l=515)

## Original application idea

> I want to build Three Feet, a safety helper for space heaters. Space heaters cause 29% of US home heating fires but 77% of the deaths, often because a blanket, curtain or box ends up too close.
>
> Two cheap cameras watch the area around the heater. SAM 3.1 outlines the heater and nearby objects so the system can measure, in inches, how close each one is. Muse Spark judges what's flammable, and Muse Wonder reads the heater's manual for its required clearances and keeps watch through the evening. If something stays too close, an ESP32 turns the heater off with its own remote and confirms it's off.
>
> I'll test it with a tape measure and real shutoffs.

The 29% and 77% figures are from NFPA's 2019–2023 estimate and include **heating stoves as well as space heaters**. See [NFPA's earlier release](https://www.nfpa.org/en/about-nfpa/press-room/news-releases/2025/us-home-heating-fires-peak-during-winter-months). Use the updated, correctly scoped figures above in the final pitch.

## Proposed prototype

The core demonstration is **perceive → reason → act → verify**:

1. Observe one fixed heater and its surroundings with a calibrated camera setup. Use SAM 3.1 to segment the heater and nearby objects.
2. Estimate an object's distance or determine whether it enters a defined clearance zone. Segmentation alone does not measure real-world distance; camera calibration, geometry, and empirical testing are required. Two cameras are a proposed approach, not yet a proven solution.
3. Use Muse Spark to help describe objects and explain potential hazards. Treat unknown objects conservatively; a model's material or flammability guess must not be the sole reason to dismiss a hazard.
4. Use Muse Wonder to extract a clearance requirement from the heater's manual, with the source visible and a person reviewing the result. Use a reviewed rule for the demonstration rather than silently trusting uncertain extraction.
5. When an object remains in the zone, issue a warning and attempt shutdown through an ESP32 and the heater's supported remote. Independently check the heater's state; distinguish an off command from a confirmed shutdown.
6. Display uncertainty and failures, including loss of camera, API, or shutdown verification. Do not present missing observations as proof of safety.

Start with one heater, a controlled camera position, and one complete working loop. Add complexity only after the basic loop is demonstrable. Use an unpowered heater or non-heating stand-in for close-object staging and demonstrate remote operation separately under supervision.

### Validation plan

- Compare distance estimates against tape measurements at multiple distances, especially around the clearance boundary; report errors and the conditions tested.
- Test multiple object types, placements, lighting conditions, and occlusions. Record missed detections and false alarms, including uncertainty cases.
- Count attempted, successful, failed, and independently confirmed shutoffs. Exercise camera and API disconnects and failed confirmation.
- Report the number of trials, setup, and limitations. Do not imply that a small demonstration establishes real-world safety or reliability.

### Demo outline

Show the physical scene alongside the interface. Move a staged object into the marked zone; show the detected object, estimate, rule and source, warning, shutdown attempt, and independent confirmation. Include an uncertainty or failure case and finish with measured results and a concise look at the code created during the event.

## Hackathon overview

- **Event:** Meta Global AI Developer Hackathon, hosted by Meta and Cerebral Valley; virtual and worldwide.
- **Building period:** October 27–November 4, 2026, according to the participant guide. The kickoff was October 26.
- **Submission deadline:** **November 4, 2026 at 9:00 p.m. Eastern Time**. The guide says late submissions will not be considered.
- **Judging:** November 5–8 in the participant guide; the public event page lists November 4–8.
- **Closing ceremony:** November 9 at 12:00 p.m. Eastern; top five per track, live final judging, winners, and community vote opening. The guide lists the community vote winner announcement for November 16.
- **Track:** Indie developer (individual builders and students) or startup (teams and founders building a product). Select the applicable track when submitting.
- **Themes:** Build for Your World or Push the Frontier. Three Feet is a proposed fit for Push the Frontier because it uses AI to understand and respond to a physical system.
- **Meta models are required:** The guide says the project must incorporate Meta models. Stage one checks that it fits a theme and reasonably uses Meta Model API and/or Meta open-weight models during the hackathon.

Dates may change. Check the [official event page](https://dev.meta.ai/events/global-hackathon), [Cerebral Valley event page](https://events.cerebralvalley.ai/e/meta-ai-developer-hackathon), participant guide, official rules, and Discord for updates. This README is a working summary, not a substitute for official rules.

## Submission and project requirements

- [ ] Build the work presented during the hackathon and clearly identify the new features, code, and functionality. The guide permits startups to build on existing products or codebases if new hackathon work is clearly identified.
- [ ] Make the repository public and confirm it is accessible to judges.
- [ ] Use Meta models and demonstrate a clear fit with a project theme.
- [ ] Keep the team to at most four participants; solo participation is allowed. List everyone who worked on the project.
- [ ] Appoint a representative if submitting as a team or startup; submit one project per team or startup through the Cerebral Valley platform.
- [ ] Provide an accessible **three-minute demo video** highlighting the specific features, code, and functionality built during the event. The guide identifies the video, code, and live product as primary judging artifacts.
- [ ] Use only code, data, and assets the team has rights to; comply with legal, ethical, and platform policies. Review the official rules and eligibility requirements.
- [ ] Verify the submission and all links before **November 4 at 9:00 p.m. Eastern**.

The guide warns that failure to clearly identify original hackathon contributions can result in disqualification. It lists basic RAG applications and simple image analyzers among generic examples to avoid. Three Feet's intended distinction is its integrated physical decision and verification loop, supported by measured results.

## Judging rubric

After a pass/fail viability and Meta model use check, projects are scored from 0–10 in each category:

| Category | Weight | Focus |
| --- | ---: | --- |
| Impact | 30% | Real-world potential, beneficiaries, usefulness, and theme fit. |
| Use of Meta AI tooling | 25% | Creative use beyond a basic integration. |
| Technical depth and execution | 20% | Sound, thoughtfully refined engineering. |
| Demo | 25% | A working, impressive demonstration and video. |

The top five projects per track advance to a final round using the same rubric. Some rubric text in the supplied PDF is horizontally clipped; consult the guide and official rules for complete wording.

## Resources and logistics

- The guide offers each accepted participant **$150 in Meta Model API credits**, claimed individually by November 4 at 9:00 p.m. Eastern. Credits are distributed in batches; check billing for application. A payment method is required to use them, and the guide notes a temporary preauthorization. A spend-limit notification can be configured in billing; verify actual billing controls and charges in the account.
- Partner credits are subject to each provider's availability and selection. The guide lists Baseten, Modal, and Fireworks; see the official event page and participant guide for current eligibility and application links.
- [Meta Model API documentation](https://dev.meta.ai/docs/overview) and [model list](https://dev.meta.ai/docs/models) provide current integration details.
- The guide lists Discord channels including `#announcements`, `#questions`, `#office-hours`, and `#team-search`; check announcements for changes. Meta office hours are listed as 10–11 a.m. Eastern on weekdays during the building period. November 3 is listed as the last full day for submission questions and troubleshooting.
- Total advertised prizes are $1 million across indie, startup, and bonus awards. See the participant guide and official rules for eligibility and award terms.

## Source material

- Participant guide: `C:/Users/nihal/Downloads/Details – Meta Global AI Developer Hackathon – Cerebral Valley.pdf` (local copy supplied for this project).
- [Meta event overview](https://dev.meta.ai/events/global-hackathon)
- [Cerebral Valley event details](https://events.cerebralvalley.ai/e/meta-ai-developer-hackathon)
- [NFPA heating safety](https://www.nfpa.org/education-and-research/home-fire-safety/heating?l=515)
