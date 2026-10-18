# FaciRob pilot preparation

Prepared 18 October 2026. This is a recruitment and observation kit, not evidence of customer traction. No invitations have been sent.

## Offer and audience

Start with ROS 2 engineers at small commercial robotics teams or integrators who recently inherited a project, integrated a component, or investigated a launch/configuration failure. The pilot offers a source-backed architecture report and a joint review of relevant findings. Do not promise that every repository contains a fault or that the tool saves a measured amount of time.

Ask for a 20-minute session on a public repository or a project the participant is authorized to evaluate locally. Do not request private source, credentials, customer recordings, or access to a running robot as the initial step. Local use still requires Docker or Python installation; exploring the exported HTML showcase requires neither.

## Materials to send once outreach is authorized

- `docs/showcase/index.html`: entry point, now leading with the real public FANUC issue case. Share the whole showcase folder or its ZIP so relative links work.
- `docs/case_studies/fanuc/README.md`: provenance, original-detector miss, generic improvement, local correction, and static verification limits for upstream issue #50. Do not imply FANUC endorsement, upstream acceptance, or a verified runtime repair.
- `docs/showcase/walkthrough.mp4`: optional controlled-example screen walkthrough, with a transcript in `docs/showcase/README.md`; this video is not footage of the FANUC case.
- `docs/showcase/broken.zip` and `fixed.zip`: reproducible synthetic projects. Only the launch filename reference differs.
- README installation instructions and SECURITY.md local deployment boundary.

The repository remains proprietary. Confirm the founder's evaluation authorization before distributing the tool or example sources to external participants. A public hosting URL has not been created; do not insert a fictional live-demo link into invitations.

## Invitation draft

Subject: Try a source-backed ROS 2 project review?

Hi [Name],

I saw your work on [specific project or integration]. I'm developing FaciRob, a local tool that maps ROS 2 repositories and flags potential integration issues with source evidence, without building the project.

I've included a case study of an existing public ROS 2 issue: a missing runtime dependency in the FANUC driver, with source evidence and a reviewed local correction. The case also explains what was verified statically and what still needs a ROS runtime check.

I'm looking for three robotics engineers to evaluate it on a project they know. We can start with a public repository, or I can help you run it locally without giving me private source access. You would get an architecture report and a joint review of any relevant findings.

Would a 20-minute session be useful?

Reza

Replace both personalisation placeholders before sending. Include the actual materials; if later hosted, use the verified URL. No sending is authorized by this document.

## Twenty-minute session

| Time | Activity | Neutral prompts |
| --- | --- | --- |
| 0–2 min | Agree on scope and permission for notes | Which project can we inspect together? Please avoid sharing confidential information. |
| 2–6 min | Understand a recent incident before showing the tool | Tell me about the last integration issue that took longer than expected. What did you inspect? Where did you get stuck? What tools helped? |
| 6–8 min | Show the public issue case | This is a pinned snapshot of a reported issue and a local correction. What does the evidence establish? What would still need checking? The controlled example is available for a simpler tutorial. |
| 8–15 min | Let the engineer use the tool | Please choose a project, run a scan, inspect something relevant, and decide what you would do next. Think aloud if comfortable. |
| 15–18 min | Check relevance and trust | Which finding is useful, incorrect, or already known? What evidence is missing? How does this compare with your existing workflow? |
| 18–20 min | Establish a concrete next step | Is there another project or incident worth trying? Who owns a decision about tools like this? |

Observe before coaching. Record every intervention so assisted completion is not counted as unaided success. If installation consumes the session, record that friction and use the saved example rather than claiming self-service adoption. User observation is formative; three sessions do not establish market demand or statistically reliable productivity gains.

## Session note template

- Participant alias / date / consent to notes:
- Role, team type, project type, ROS distribution:
- Public project or local-only evaluation (do not record private paths):
- Recent incident and tools used:
- Setup time and failures:
- Could the engineer explain the value before scanning? What did they say?
- Could they start a scan without help?
- Could they find a relevant result and its source evidence without help?
- Could they distinguish static findings from runtime verification?
- Finding reviewed: code, engineer assessment, reason; omit confidential snippets:
- Wrong, missing, redundant, or distracting findings:
- Proposed next action and whether it was appropriate:
- Founder interventions and time:
- Explicit request to use the tool on another project:
- Purchasing process or willingness-to-pay evidence (quote actual terms; do not infer from praise):
- Agreed follow-up, owner, and authorization:

Store identifiable participant notes outside the public repository. Summaries or case studies require separate permission and anonymization.

## Initial decision criteria

Targets, not achieved results: three relevant engineers complete the core task with minimal assistance, can accurately describe the verification boundary, and express a concrete reason to evaluate a second project. Review blockers after every session. If engineers understand the report but find it irrelevant, revisit the problem and target segment before extending the GUI.

For later time-savings evaluation, compare time to a correct verified resolution with normal practice, including coding assistants. Include review/correction time, healthy cases, unfamiliar repositories, false alarms, and founder effort. Do not turn the controlled demo into a customer productivity claim.
