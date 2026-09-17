import { requireAdmin } from "@/lib/authz";
import { listUsers } from "@/lib/notes-db";
import { UsersTable } from "./users-table";

export default async function AdminPage() {
  await requireAdmin();
  const users = listUsers();
  return (
    <div>
      <h1>Users</h1>
      <UsersTable initialUsers={users} />
    </div>
  );
}
