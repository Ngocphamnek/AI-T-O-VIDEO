import { Router, type IRouter } from "express";
import http from "node:http";

const router: IRouter = Router();

/**
 * Proxy /api/auth/callback → bot FastAPI server on port 3000 (/auth/callback)
 */
router.get("/auth/callback", (req, res) => {
  const query = req.url.includes("?") ? req.url.slice(req.url.indexOf("?")) : "";
  const options = {
    hostname: "127.0.0.1",
    port: 3000,
    path: `/auth/callback${query}`,
    method: "GET",
    headers: { ...req.headers, host: "localhost:3000" },
  };

  const proxy = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
    proxyRes.pipe(res, { end: true });
  });

  proxy.on("error", (err) => {
    res.status(502).send(`Bot OAuth server unavailable: ${err.message}`);
  });

  req.pipe(proxy, { end: true });
});

export default router;
