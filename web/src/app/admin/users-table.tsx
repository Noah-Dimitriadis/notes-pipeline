"use client";

import { useState } from "react";

interface AppUser {
  email: string;
  disabled: boolean;
  is_admin: boolean;
  has_key: boolean;
  created_at: string;
}

export function UsersTable({ initialUsers }: { initialUsers: AppUser[] }) {
  const [users, setUsers] = useState(initialUsers);
  const [newEmail, setNewEmail] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    const res = await fetch("/api/admin/users", { cache: "no-store" });
    if (res.ok) setUsers(await res.json());
  }

  async function handleAdd(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const res = await fetch("/api/admin/users", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: newEmail }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.error || "Could not add user.");
      return;
    }
    setNewEmail("");
    await refresh();
  }

  async function toggleDisabled(email: string, disabled: boolean) {
    await fetch(`/api/admin/users/${encodeURIComponent(email)}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ disabled }),
    });
    await refresh();
  }

  return (
    <div>
      <form onSubmit={handleAdd} style={{ marginBottom: "1.5rem" }}>
        {error && <p className="error-message">{error}</p>}
        <div className="field">
          <label htmlFor="new_email">Add a friend by email</label>
          <input
            type="text"
            id="new_email"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            placeholder="friend@example.com"
            required
          />
        </div>
        <button type="submit">Add</button>
      </form>

      <table>
        <thead>
          <tr>
            <th>Email</th>
            <th>Created</th>
            <th>Has key</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.email}>
              <td>{u.email}</td>
              <td>{new Date(u.created_at).toLocaleDateString()}</td>
              <td>{u.has_key ? "yes" : "no"}</td>
              <td>
                <span className="badge">{u.disabled ? "disabled" : "active"}</span>
              </td>
              <td>
                <button onClick={() => toggleDisabled(u.email, !u.disabled)}>
                  {u.disabled ? "Re-enable" : "Disable"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
