// Loaded by the BlueMap webapp via webapp.conf `scripts`; styled by ore-ui.css.
// Adds a way back to the status page, and the disclaimer the Minecraft Usage
// Guidelines require on every page using game assets.
(() => {
  const back = document.createElement("a");
  back.className = "mcs-back";
  back.href = "../";
  back.innerHTML = '<img src="../assets/mc/item/grass_block.png" alt=""><span>Back to status</span>';
  back.setAttribute("aria-label", "Back to status");

  const note = document.createElement("div");
  note.className = "mcs-disclaimer";
  note.setAttribute("role", "note");
  note.textContent = "NOT AN OFFICIAL MINECRAFT WEBSITE. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT.";

  document.body.append(back, note);
})();
