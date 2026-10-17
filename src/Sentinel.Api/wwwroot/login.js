"use strict";
const form = document.getElementById("login-form");
const password = document.getElementById("password");
const errorMessage = document.getElementById("login-error");
const submit = document.getElementById("sign-in");
document.getElementById("toggle-password").addEventListener("click", event => {
  const visible = password.type === "password";
  password.type = visible ? "text" : "password";
  event.currentTarget.setAttribute("aria-pressed", String(visible));
  event.currentTarget.setAttribute("aria-label", visible ? "비밀번호 숨기기" : "비밀번호 표시");
});
for (const type of ["keydown", "keyup"]) password.addEventListener(type, event => {
  document.getElementById("caps-lock").hidden = !event.getModifierState("CapsLock");
});
form.addEventListener("submit", async event => {
  event.preventDefault();
  if (submit.disabled) return;
  errorMessage.textContent = "";
  submit.disabled = true;
  submit.querySelector("span").textContent = "확인 중";
  form.setAttribute("aria-busy", "true");
  try {
    const csrf = await fetch("/auth/csrf", {cache:"no-store", signal:AbortSignal.timeout(10000)});
    if (!csrf.ok) throw new Error("CONNECTION");
    const {request_token} = await csrf.json();
    const response = await fetch("/auth/login", {
      method:"POST", headers:{"Content-Type":"application/json", "X-CSRF-TOKEN":request_token},
      body:JSON.stringify({username:document.getElementById("username").value, password:password.value}),
      signal:AbortSignal.timeout(15000),
    });
    if (response.ok) { password.value = ""; location.replace("/"); return; }
    errorMessage.textContent = response.status === 429 ? "로그인 시도가 많습니다 · 1분 뒤 다시 시도해 주세요" :
      response.status === 401 ? "아이디 또는 비밀번호를 확인해 주세요" : "로그인 정보를 다시 확인해 주세요";
    password.focus();
  } catch {
    errorMessage.textContent = "서버에 연결하지 못했습니다 · 잠시 후 다시 시도해 주세요";
  } finally {
    submit.disabled = false; submit.querySelector("span").textContent = "로그인"; form.removeAttribute("aria-busy");
  }
});
