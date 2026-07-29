process.env.HOSTNAME ||= process.env.MAFIA_WEB_HOST || "127.0.0.1";
process.env.PORT ||= process.env.MAFIA_WEB_PORT || "3000";

require("./server.js");
