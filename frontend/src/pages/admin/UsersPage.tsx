import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import CreateUserModal from "../../components/CreateUserModal";
import TokenModal from "../../components/TokenModal";
import { useAuth } from "../../hooks/useAuth";
import { extractErrorMessage, formatDate } from "../../utils/presentation";

type AdminUser = {
  id: string;
  username: string;
  email: string;
  is_admin: boolean;
  is_active: boolean;
  token_balance: number;
  created_at: string;
  updated_at: string;
};

type AdminUsersListResponse = {
  items: AdminUser[];
  total: number;
  skip: number;
  limit: number;
};

type AdminCreateUserResponse = {
  user: AdminUser;
  generated_password: string | null;
};

const PAGE_LIMIT = 100;
const adminUsersQueryKey = (userId: string | undefined) => ["admin-users", userId] as const;

async function fetchUsersPage(skip: number, limit: number): Promise<AdminUsersListResponse> {
  const response = await apiClient.get<AdminUsersListResponse>("/admin/users", {
    params: { skip, limit },
  });
  return response.data;
}

export default function UsersPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [tokenUser, setTokenUser] = useState<AdminUser | null>(null);
  const [busyUserIds, setBusyUserIds] = useState<Set<string>>(new Set());
  const [items, setItems] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasHydrated, setHasHydrated] = useState(false);
  const userId = user?.user_id;

  const usersQuery = useQuery({
    queryKey: adminUsersQueryKey(userId),
    enabled: Boolean(user?.is_admin),
    queryFn: async () => fetchUsersPage(0, PAGE_LIMIT),
  });

  useEffect(() => {
    setItems([]);
    setTotal(0);
    setHasHydrated(false);
  }, [userId]);

  useEffect(() => {
    if (!usersQuery.data) {
      return;
    }
    setTotal(usersQuery.data.total);
    setItems((previous) => {
      const firstPageItems = usersQuery.data.items;
      if (!hasHydrated || previous.length === 0) {
        return firstPageItems;
      }
      const firstPageIds = new Set(firstPageItems.map((item) => item.id));
      const tail = previous.slice(firstPageItems.length).filter((item) => !firstPageIds.has(item.id));
      return [...firstPageItems, ...tail].slice(0, usersQuery.data.total);
    });
    setHasHydrated(true);
  }, [hasHydrated, usersQuery.data]);

  const createUserMutation = useMutation({
    mutationFn: async (payload: {
      username: string;
      email: string;
      password?: string;
      generate_password: boolean;
      is_admin: boolean;
      is_active: boolean;
    }) => {
      const response = await apiClient.post<AdminCreateUserResponse>("/admin/users", payload);
      return response.data;
    },
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: adminUsersQueryKey(userId) });
      setIsCreateModalOpen(false);
      if (result.generated_password) {
        setActionSuccess(`User created. Generated password: ${result.generated_password}`);
      } else {
        setActionSuccess("User created.");
      }
    },
    onError: (error) => {
      setActionError(extractErrorMessage(error, "Failed to create user."));
    },
  });

  const updateUserMutation = useMutation({
    mutationFn: async (payload: { userId: string; is_active?: boolean; is_admin?: boolean }) => {
      const { userId: id, ...body } = payload;
      await apiClient.patch(`/admin/users/${id}`, body);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: adminUsersQueryKey(userId) });
    },
  });

  const deactivateUserMutation = useMutation({
    mutationFn: async (targetUserId: string) => {
      await apiClient.delete(`/admin/users/${targetUserId}`);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: adminUsersQueryKey(userId) });
    },
  });

  const addTokensMutation = useMutation({
    mutationFn: async (payload: { userId: string; amount: number; reason: string }) => {
      const { userId: targetUserId, ...body } = payload;
      await apiClient.post(`/admin/users/${targetUserId}/tokens`, body);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: adminUsersQueryKey(userId) });
      setTokenUser(null);
      setActionSuccess("Tokens added.");
    },
    onError: (error) => {
      setActionError(extractErrorMessage(error, "Failed to add tokens."));
    },
  });

  const filteredUsers = useMemo(() => {
    const value = search.trim().toLowerCase();
    if (!value) {
      return items;
    }
    return items.filter((item) => item.username.toLowerCase().includes(value) || item.email.toLowerCase().includes(value));
  }, [items, search]);

  async function runUserAction(targetUserId: string, action: () => Promise<void>) {
    if (busyUserIds.has(targetUserId)) {
      return;
    }
    setActionError(null);
    setActionSuccess(null);
    setBusyUserIds((previous) => {
      const next = new Set(previous);
      next.add(targetUserId);
      return next;
    });
    try {
      await action();
    } catch (error) {
      setActionError(extractErrorMessage(error, "User action failed."));
    } finally {
      setBusyUserIds((previous) => {
        const next = new Set(previous);
        next.delete(targetUserId);
        return next;
      });
    }
  }

  function isBusy(userRowId: string): boolean {
    return busyUserIds.has(userRowId);
  }

  async function handleLoadMore() {
    if (isLoadingMore || items.length >= total) {
      return;
    }
    setActionError(null);
    setIsLoadingMore(true);
    try {
      const page = await fetchUsersPage(items.length, PAGE_LIMIT);
      setItems((previous) => {
        const existingIds = new Set(previous.map((item) => item.id));
        const nextItems = page.items.filter((item) => !existingIds.has(item.id));
        return [...previous, ...nextItems];
      });
      setTotal(page.total);
    } catch (error) {
      setActionError(extractErrorMessage(error, "Failed to load more users."));
    } finally {
      setIsLoadingMore(false);
    }
  }

  return (
    <section style={{ display: "grid", gap: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.75rem" }}>
        <h2 style={{ margin: 0 }}>Admin Users</h2>
        <button onClick={() => setIsCreateModalOpen(true)} type="button">
          Create user
        </button>
      </div>

      <label htmlFor="admin-users-search">
        Search by username/email{" "}
        <input
          id="admin-users-search"
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search user"
          type="search"
          value={search}
        />
      </label>

      {actionError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {actionError}
        </p>
      ) : null}
      {actionSuccess ? (
        <p style={{ color: "#166534", margin: 0 }} role="status">
          {actionSuccess}
        </p>
      ) : null}

      {usersQuery.isLoading ? <p>Loading users...</p> : null}
      {usersQuery.isError ? (
        <p style={{ color: "#b00020", margin: 0 }} role="alert">
          {extractErrorMessage(usersQuery.error, "Failed to load users.")}
        </p>
      ) : null}

      {!usersQuery.isLoading && !usersQuery.isError && filteredUsers.length === 0 ? <p>No users found.</p> : null}

      {!usersQuery.isLoading && !usersQuery.isError && filteredUsers.length > 0 ? (
        <>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th align="left">Username</th>
                <th align="left">Email</th>
                <th align="left">Status</th>
                <th align="left">Created</th>
                <th align="left">Tokens</th>
                <th align="left">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredUsers.map((row) => (
                <tr key={row.id}>
                  <td>
                    {row.username} {row.is_admin ? "(admin)" : ""}
                  </td>
                  <td>{row.email}</td>
                  <td>{row.is_active ? "active" : "inactive"}</td>
                  <td>{formatDate(row.created_at)}</td>
                  <td>{row.token_balance}</td>
                  <td style={{ display: "flex", flexWrap: "wrap", gap: "0.35rem" }}>
                    <button
                      disabled={isBusy(row.id) || row.id === userId}
                      onClick={() =>
                        runUserAction(row.id, async () => {
                          await updateUserMutation.mutateAsync({ userId: row.id, is_active: !row.is_active });
                        })
                      }
                      type="button"
                    >
                      {row.is_active ? "Deactivate" : "Activate"}
                    </button>
                    <button
                      disabled={isBusy(row.id) || row.id === userId}
                      onClick={() =>
                        runUserAction(row.id, async () => {
                          await deactivateUserMutation.mutateAsync(row.id);
                        })
                      }
                      type="button"
                    >
                      Delete
                    </button>
                    <button
                      disabled={isBusy(row.id)}
                      onClick={() => {
                        setActionError(null);
                        setActionSuccess(null);
                        setTokenUser(row);
                      }}
                      type="button"
                    >
                      Add tokens
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {items.length < total ? (
            <button disabled={isLoadingMore} onClick={handleLoadMore} type="button">
              {isLoadingMore ? "Loading..." : "Load more"}
            </button>
          ) : null}
        </>
      ) : null}

      <CreateUserModal
        isOpen={isCreateModalOpen}
        isSubmitting={createUserMutation.isPending}
        onClose={() => {
          if (!createUserMutation.isPending) {
            setIsCreateModalOpen(false);
          }
        }}
        onSubmit={async (payload) => {
          setActionError(null);
          setActionSuccess(null);
          try {
            await createUserMutation.mutateAsync(payload);
          } catch (error) {
            setActionError(extractErrorMessage(error, "Failed to create user."));
          }
        }}
      />

      <TokenModal
        isOpen={Boolean(tokenUser)}
        isSubmitting={addTokensMutation.isPending}
        onClose={() => {
          if (!addTokensMutation.isPending) {
            setTokenUser(null);
          }
        }}
        onSubmit={async ({ amount, reason }) => {
          if (!tokenUser) {
            return;
          }
          setActionError(null);
          setActionSuccess(null);
          try {
            await addTokensMutation.mutateAsync({ userId: tokenUser.id, amount, reason });
          } catch (error) {
            setActionError(extractErrorMessage(error, "Failed to add tokens."));
          }
        }}
        username={tokenUser?.username}
      />
    </section>
  );
}
