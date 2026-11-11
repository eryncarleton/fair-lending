// Cloudflare Worker: serves the app at https://ecarleton.com/redlining by forwarding to Cloud Run.
//
// Set up once in the Cloudflare dashboard (see README > Hosting):
//   * Workers & Pages > Create > Worker named "fair-lending-redlining", paste this file, Deploy
//   * Worker > Settings > Domains & Routes > Add route: ecarleton.com/redlining*  (zone ecarleton.com)
//   * DNS: a proxied (orange cloud) record for ecarleton.com, e.g. AAAA  @  100::
//
// The app itself runs with Streamlit's base path set to /redlining (deploy/cloudrun.sh), so paths
// pass through unchanged. Streamlit's live connection is a WebSocket; fetch() passes the upgrade through.

const UPSTREAM = "https://fair-lending-542044103226.us-west1.run.app";
const BASE = "/redlining";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === BASE) {
      return Response.redirect(`${url.origin}${BASE}/${url.search}`, 301);
    }
    if (!url.pathname.startsWith(`${BASE}/`)) {
      return new Response("Not found", { status: 404 });
    }
    const target = new URL(url.pathname + url.search, UPSTREAM);
    return fetch(new Request(target, request));
  },
};
