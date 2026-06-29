// Readable source for the "Grab FB listings" bookmarklet embedded (minified)
// in web/index.html's #fb-bookmark anchor. Edit here, then minify to one line
// and paste into that href if Facebook's markup changes.
//
// It runs in the user's logged-in Facebook tab, reads the Marketplace listing
// cards already rendered on screen, and copies them to the clipboard as JSON
// for VanApt's "Import from Facebook" paste box (POST /api/import_bulk).
(function () {
  var seen = {}, items = [];
  document.querySelectorAll('a[href*="/marketplace/item/"]').forEach(function (a) {
    var m = a.href.match(/\/marketplace\/item\/(\d+)/);
    if (!m) return;
    var id = m[1];
    if (seen[id]) return;
    seen[id] = 1;
    var L = (a.innerText || "").split("\n").map(function (x) { return x.trim(); }).filter(Boolean);
    // Cards read like: ["CA$1,200", "1 Bed 1 Bath - House", "Vancouver, BC"]
    var pi = L.findIndex(function (x) { return /[$]\s?[\d,]/.test(x); });
    var pl = pi >= 0 ? L[pi] : "";
    var pm = pl.match(/[$]\s?([\d,]+)/);
    var pr = pm ? Number(pm[1].replace(/,/g, "")) : null;
    var t = (pi >= 0 && L[pi + 1]) ? L[pi + 1] : "";
    if (!t) { t = L.filter(function (_, i) { return i !== pi; }).sort(function (a, b) { return b.length - a.length; })[0] || ""; }
    var lo = (pi >= 0 && L[pi + 2]) ? L[pi + 2] : "";
    var im = a.querySelector("img");
    items.push({
      url: "https://www.facebook.com/marketplace/item/" + id + "/",
      title: t, price: pr, neighborhood: lo,
      image_url: im ? im.src : "", source: "facebook",
    });
  });
  if (!items.length) {
    alert("No Marketplace listings found here. Open a Marketplace search/results page, scroll down so listings load, then click again.");
    return;
  }
  var j = JSON.stringify(items);
  function done() { alert(items.length + " listing(s) copied! Now open VanApt, click Import from Facebook, paste (Ctrl+V), and Import."); }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(j).then(done, function () {
      window.prompt("Copy this text (Ctrl+C), then paste into VanApt:", j);
    });
  } else {
    window.prompt("Copy this text (Ctrl+C), then paste into VanApt:", j);
  }
})();
