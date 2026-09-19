// Demo mode: open the page with ?demo and every card renders from a fixture
// instead of the live connection. No agent, no Worker, no mod, no game.
//
// It exists to split one question into two. When a card is missing, it is either
// the page's fault or the data never arrived — and telling those apart has meant
// guessing at which of the mod, the agent and the deploy is behind. In demo mode
// the page is the only variable: if a card shows up here and not on your real
// page, the page is fine and something upstream isn't sending it.
//
// Nothing here is real. It never touches the network.
//
// The fixture itself lives in demo-data.js, which knows nothing about browsers,
// so tools/fake_agent.py can push the same data at a local Worker. This file is
// only the browser half: which mode the URL asked for, and what imagery exists.
import { DEMO_BASE, demoShots as buildShots, demoStatus } from "./demo-data.js";

const params = new URLSearchParams(location.search);
export const isDemo = params.has("demo");
// The hero frame and the panorama are mutually exclusive on the real page — the
// panorama only appears once you've logged out. ?demo=offline shows that half.
export const isLoggedOutDemo = params.get("demo") === "offline";

// Whether the imagery was injected at deploy. It is the maintainer's own world, so it
// is deliberately not in the repo — a fork has none, and drawing eight broken images
// is a worse demo than drawing no archive at all.
const probe = (path) => new Promise((resolve) => {
  const image = new Image();
  image.onload = () => resolve(true);
  image.onerror = () => resolve(false);
  image.src = path;
});

export async function demoAssets() {
  const [frames, panorama] = await Promise.all([
    probe(`${DEMO_BASE}/frame-0.webp`), probe(`${DEMO_BASE}/panorama.webp`)]);
  return { frames, panorama };
}

export const demoData = (have = { frames: true, panorama: true }) =>
  demoStatus({ loggedOut: isLoggedOutDemo, have });

export const demoShots = () => buildShots();
