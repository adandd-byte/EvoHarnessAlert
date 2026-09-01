const AUTH_KEY = "evoharness.alert.auth";
const form = document.querySelector("#loginForm");
const state = document.querySelector("#loginState");
form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = document.querySelector("#username").value.trim();
  const password = document.querySelector("#password").value;
  const token = btoa(`${username}:${password}`);
  try {
    const response = await fetch("/api/profile", { headers: { Authorization: `Basic ${token}` } });
    if (!response.ok) throw new Error("账号或密码不正确");
    sessionStorage.setItem(AUTH_KEY, JSON.stringify({ token }));
    location.href = "/admin.html";
  } catch (error) {
    state.textContent = error.message;
  }
});
