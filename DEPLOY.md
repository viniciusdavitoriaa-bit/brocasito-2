# 🚀 Deploy Guide — br! Cargo Bot

## Pré-requisitos

- Conta no [Discord Developer Portal](https://discord.com/developers/applications)
- Conta no [Railway](https://railway.app) ou [Render](https://render.com)
- Conta no [GitHub](https://github.com)

---

## 1. Criar o Bot no Discord

1. Acesse https://discord.com/developers/applications
2. Clique em **New Application** → dê um nome ao bot
3. Vá em **Bot** → clique em **Add Bot**
4. Em **Token** → clique em **Reset Token** → copie o token (guarde bem!)
5. Em **Privileged Gateway Intents**, ative:
   - ✅ Server Members Intent
   - ✅ Message Content Intent
   - ✅ Presence Intent
6. Salve as alterações

### Convidar o bot para o servidor

Vá em **OAuth2 → URL Generator**:
- Scope: `bot`
- Bot Permissions: `Manage Roles`, `Send Messages`, `Read Message History`,
  `Use External Emojis`, `Embed Links`, `Read Messages/View Channels`

Copie a URL gerada e abra no navegador para adicionar ao servidor.

> ⚠️ O cargo do bot no servidor deve estar **acima** dos cargos que ele vai gerenciar.

---

## 2. Subir para o GitHub

```bash
git init
git add .
git commit -m "feat: br! cargo bot inicial"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/SEU_REPO.git
git push -u origin main
```

---

## 3. Deploy no Railway

1. Acesse https://railway.app → **New Project → Deploy from GitHub repo**
2. Selecione o repositório
3. Vá em **Variables** e adicione:
   ```
   DISCORD_TOKEN = seu_token_aqui
   ```
4. O deploy inicia automaticamente usando `railway.json` + `nixpacks.toml`
5. Certifique-se de que o serviço está como **Worker** (sem porta exposta)

---

## 4. Deploy no Render (alternativa)

1. Acesse https://render.com → **New → Background Worker**
2. Conecte o repositório GitHub
3. O `render.yaml` já configura tudo automaticamente
4. Adicione a variável de ambiente:
   ```
   DISCORD_TOKEN = seu_token_aqui
   ```
5. Clique em **Create Background Worker**

---

## 5. Comandos do Bot

| Comando | Quem pode usar | Descrição |
|---|---|---|
| `br!painel @user` | Staff | Abre painel para setar cargo + servidor |
| `br!setcargo @cargo` | Dono | Define qual cargo pode usar `br!painel` |
| `br!settempo` | Dono | Define tempo de expiração (30/60/90 dias) |
| `br!log [#canal]` | Dono | Define canal de log |
| `br!help` | Todos | Lista os comandos |

### Fluxo de uso

1. Dono usa `br!setcargo @Staff` para dar permissão ao cargo de staff
2. Dono usa `br!settempo` para escolher 30, 60 ou 90 dias
3. Dono usa `br!log #canal-de-logs` para configurar o log
4. Staff usa `br!painel @usuario` → clica nos botões para setar cargo e servidor
5. Quando o tempo expirar, o bot:
   - Remove o cargo do usuário
   - Envia DM para o usuário notificado fixo (embed preta)
   - Envia DM para o usuário que teve o cargo removido
   - Registra no canal de log

---

## 6. Banco de Dados

O bot usa **SQLite** (`bot_data.db`) localmente.
No Railway/Render, o arquivo persiste enquanto o serviço existir.

> Para persistência permanente no Railway, considere adicionar um volume em
> **Settings → Volumes** apontando para `/app`.

---

## 7. Variáveis de Ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Token do bot no Discord Developer Portal |
