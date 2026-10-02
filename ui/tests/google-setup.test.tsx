import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../src/api/client";
import { GoogleSetup } from "../src/screens/connections/CredentialForms";

function Setup({ configured = false }) {
  const [status, setStatus] = useState({ configured });
  return <GoogleSetup status={status} onSave={setStatus} />;
}
const credentials = { client_id: "demo.apps.googleusercontent.com", client_secret: "synthetic-client-secret" };
function upload(contents: string) {
  const input = screen.getByLabelText("Upload the JSON file Google gave you");
  fireEvent.change(input, { target: { files: [new File([contents], "client.json", { type: "application/json" })] } });
  return input;
}

describe("Google client upload", () => {
  it.each(["installed", "web"])("reads %s credentials locally and saves through the existing endpoint", async type => {
    const call = vi.spyOn(api, "call").mockResolvedValue({ configured: true });
    const storage = vi.spyOn(Storage.prototype, "setItem");
    render(<Setup />);
    expect(screen.getByText("Paste the ID and secret instead").closest("details")).not.toHaveAttribute("open");
    const input = upload(JSON.stringify({ [type]: credentials }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save Google client" })).toBeEnabled());
    expect(screen.getByLabelText("Google client ID")).toHaveValue(credentials.client_id);
    expect(screen.getByLabelText("Google client secret")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Google client secret")).not.toBeVisible();
    expect(input).toHaveValue("");
    expect(call).not.toHaveBeenCalled();
    if (type === "web") expect(screen.getByRole("alert")).toHaveTextContent(/Desktop app/);
    else expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save Google client" }));
    expect(await screen.findByText("Google client saved")).toBeVisible();
    expect(call).toHaveBeenCalledWith("google_client_set", { body: credentials });
    expect(storage).not.toHaveBeenCalled();
    expect(document.body.innerHTML).not.toContain(credentials.client_secret);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Replace" })).toHaveFocus());
  });

  it.each(["not json", "null", "[]", "{}", '{"installed":{"client_id":"id"}}', '{"installed":{"client_id":3,"client_secret":"secret"}}', '{"installed":{"client_id":" ","client_secret":"secret"}}']) ("rejects malformed credentials: %s", async contents => {
    const call = vi.spyOn(api, "call");
    render(<Setup />);
    upload(contents);
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a Google client JSON file containing a client ID and secret.");
    expect(screen.getByRole("button", { name: "Save Google client" })).toBeDisabled();
    expect(call).not.toHaveBeenCalled();
  });

  it("rejects files larger than 64 KB before reading them", async () => {
    const read = vi.spyOn(FileReader.prototype, "readAsText");
    render(<Setup />);
    upload(" ".repeat(64 * 1024 + 1));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a JSON file no larger than 64 KB.");
    expect(read).not.toHaveBeenCalled();
  });

  it("collapses an already saved client and allows replacement without exposing old credentials", () => {
    render(<Setup configured />);
    expect(screen.getByText("Google client saved")).toBeVisible();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Google client secret")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Replace" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByLabelText("Google client secret")).toHaveValue("");
    expect(screen.getByLabelText("Upload the JSON file Google gave you")).toHaveFocus();
  });
});
