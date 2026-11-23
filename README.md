![Standing Order Job](assets/Standing_Order_Job_readme_banner.png)

# Standing Order Job - A meitou client mod for Kenshi

Pin squadmates to a spot and keep them there. Hold right-click a player character and choose Stand Here for a one-shot move, or hold Shift to assign a permanent Stand Here job — the same context-menu convention Kenshi already uses for other jobs.

## Features

- Adds `Stand Here` to a player character's hold-right-click menu when they are eligible for player orders (selected, non-animal).
- Unshifted `Stand Here` orders selected eligible characters to the clicked character's current position once.
- Shift+`Stand Here` adds or updates a visible permanent `Stand Here` job; each job owns one standing position.
- Characters walk to their assigned point, then hold until a higher-priority interruption or a player order replaces or removes the job.
- Combat, incapacity, carrying, and other standard interruptions use normal task precedence; eligible characters resume standing afterward.
- Respects the Jobs toggle and can be removed from the normal Jobs panel.
- Assigned positions survive save/load.
- Job list order matters: an always-eligible `Stand Here` blocks later jobs below it (same class of behaviour as Bodyguard). Reassigning updates the job in place so it keeps its index.

## Screenshots

Screenshots live under `assets/screenshots/` in this mod repository (documentation assets; not package payload unless a packager copies them).

![Screenshot placeholder](assets/screenshots/placeholder_1080p.png)

## Requirements

- **[Meitou Client](https://github.com/MeitouEngine/MeitouClient)** (latest compatible build for `>=0.22.2 <0.23.0`)
- Standalone: no additional external mod dependencies or Workshop downloads required.

## AI disclosure

This mod is designed, architected, and reviewed by me, but much of the code is AI-generated. Documentation and supporting development tools also contain AI-generated content. <u>**This mod does not contain any AI generated art, models, prose, or audio.**</u>

I respect the strong feelings surrounding AI and deeply share the concerns about its impact on artists, workers, communities, and the environment. These are real problems that deserve meaningful action. I encourage those who feel strongly about these issues to push for protections and accountability through their local and national governments.

For me, AI is also a tool that makes creative projects like this more accessible. I work a demanding job, and AI allows me to put my 14 years of software development experience and love for these games toward bringing ideas I'd like to see in the game to life. Without it, many of these ideas would likely remain just that. I'm glad to be able to share them, and I hope you enjoy them too.

## Documentation

- `docs/package.md`: acceptance brief, package/stage instructions, and debug trace.
- `docs/implementation-notes.md`: implementation and validation notes.
- `AGENTS.md`: mod-local agent guidance complementary to the Factory skills.
