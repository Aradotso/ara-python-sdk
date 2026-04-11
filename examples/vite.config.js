import { resolve } from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    rollupOptions: {
      input: {
        canonicalEmailChatCron: resolve(
          __dirname,
          "frontend/02-canonical-email-chat-cron/index.html",
        ),
      },
    },
  },
});
