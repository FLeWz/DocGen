import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import "./app.css"; // after the components so the shell overrides editor.css defaults
import { UIProvider } from "./ui";

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <BrowserRouter><UIProvider><App /></UIProvider></BrowserRouter>
  </QueryClientProvider>,
);
