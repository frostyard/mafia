process.env.HOSTNAME ||= process.env.MAFIA_WEB_HOST || "127.0.0.1";
process.env.PORT ||= process.env.MAFIA_WEB_PORT || "3000";

if (
  process.env.MAFIA_AUTH_MODE === "github" &&
  !process.env.MAFIA_INTERNAL_SECRET
) {
  throw new Error(
    "MAFIA_INTERNAL_SECRET is required when GitHub authentication is enabled",
  );
}

require("./server.js");
