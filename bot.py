import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Select, Modal, TextInput
import sqlite3
import asyncio
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ── Fuso horário de Brasília ──────────────────────────────────────────────────
BRT = ZoneInfo("America/Sao_Paulo")

def now_brt() -> datetime:
    """Retorna o datetime atual no horário de Brasília (UTC-3)."""
    return datetime.now(BRT)

# ── Config ───────────────────────────────────────────────────────────────────
TOKEN           = os.getenv("DISCORD_TOKEN", "")
PREFIX          = "br!"
NOTIFY_USER_IDS = [1514659038632345752, 1513806388374143084, 1513831827146539137]

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = "bot_data.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            guild_id       INTEGER PRIMARY KEY,
            staff_role_id  INTEGER DEFAULT NULL,
            log_channel_id INTEGER DEFAULT NULL,
            tempo_dias     INTEGER DEFAULT 30
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cargos_setados (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id         INTEGER NOT NULL,
            user_id          INTEGER NOT NULL,
            cargo_id         INTEGER NOT NULL,
            servidor_nome    TEXT    NOT NULL,
            servidor_id      TEXT    DEFAULT NULL,
            data_setado      TEXT    NOT NULL,
            data_expiracao   TEXT    DEFAULT NULL,
            setado_por       INTEGER NOT NULL,
            removido         INTEGER DEFAULT 0,
            data_removido    TEXT    DEFAULT NULL,
            tempo_permanente INTEGER DEFAULT 0
        )
    """)
    # Tabela de tempo por cargo
    c.execute("""
        CREATE TABLE IF NOT EXISTS role_tempo (
            guild_id  INTEGER NOT NULL,
            cargo_id  INTEGER NOT NULL,
            tempo_dias INTEGER NOT NULL,
            PRIMARY KEY (guild_id, cargo_id)
        )
    """)
    # Migrações seguras para bancos antigos
    for col, definition in [
        ("servidor_id",      "TEXT DEFAULT NULL"),
        ("tempo_permanente", "INTEGER DEFAULT 0"),
        ("data_expiracao",   "TEXT DEFAULT NULL"),
    ]:
        try:
            c.execute(f"ALTER TABLE cargos_setados ADD COLUMN {col} {definition}")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

def get_config(guild_id: int) -> dict:
    conn = get_db()
    row  = conn.execute("SELECT * FROM config WHERE guild_id = ?", (guild_id,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"guild_id": guild_id, "staff_role_id": None, "log_channel_id": None, "tempo_dias": 30}

def upsert_config(guild_id: int, **kwargs):
    conn = get_db()
    cfg  = get_config(guild_id)
    cfg.update(kwargs)
    conn.execute("""
        INSERT INTO config (guild_id, staff_role_id, log_channel_id, tempo_dias)
        VALUES (:guild_id, :staff_role_id, :log_channel_id, :tempo_dias)
        ON CONFLICT(guild_id) DO UPDATE SET
            staff_role_id  = excluded.staff_role_id,
            log_channel_id = excluded.log_channel_id,
            tempo_dias     = excluded.tempo_dias
    """, cfg)
    conn.commit()
    conn.close()

def get_role_tempo(guild_id: int, cargo_id: int) -> int | None:
    """Retorna o tempo em dias para o cargo específico, ou None se não configurado.
    -1 = permanente."""
    conn = get_db()
    row  = conn.execute(
        "SELECT tempo_dias FROM role_tempo WHERE guild_id = ? AND cargo_id = ?",
        (guild_id, cargo_id)
    ).fetchone()
    conn.close()
    return row["tempo_dias"] if row else None

def set_role_tempo(guild_id: int, cargo_id: int, tempo_dias: int):
    """Define o tempo para um cargo específico. -1 = permanente."""
    conn = get_db()
    conn.execute("""
        INSERT INTO role_tempo (guild_id, cargo_id, tempo_dias)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, cargo_id) DO UPDATE SET tempo_dias = excluded.tempo_dias
    """, (guild_id, cargo_id, tempo_dias))
    conn.commit()
    conn.close()

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.all()
bot     = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_dt(dt_str: str | None) -> str:
    if not dt_str:
        return "—"
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return dt_str

def tempo_str(tempo: int) -> str:
    if tempo == -1:
        return "Permanente"
    if tempo == 0:
        return "30 segundos"
    return f"{tempo} dias"

def tempo_timedelta(tempo: int) -> timedelta | None:
    """Retorna None para permanente."""
    if tempo == -1:
        return None
    if tempo == 0:
        return timedelta(seconds=30)
    return timedelta(days=tempo)

def tempo_restante_str(data_expiracao: str | None, permanente: int) -> str:
    if permanente:
        return "♾️ Permanente"
    if not data_expiracao:
        return "Desconhecido"
    try:
        exp = datetime.fromisoformat(data_expiracao)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=BRT)
        diff = exp - now_brt()
        if diff.total_seconds() <= 0:
            return "Expirado"
        total_s = int(diff.total_seconds())
        dias    = total_s // 86400
        horas   = (total_s % 86400) // 3600
        minutos = (total_s % 3600) // 60
        if dias > 0:
            return f"{dias}d {horas}h {minutos}m"
        if horas > 0:
            return f"{horas}h {minutos}m"
        return f"{minutos}m"
    except Exception:
        return "—"

def make_base_embed(guild: discord.Guild, title: str, color: discord.Color) -> discord.Embed:
    embed = discord.Embed(title=title, color=color, timestamp=now_brt())
    if guild and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=guild.name if guild else "Discord Bot")
    return embed

async def send_log(guild: discord.Guild, embed: discord.Embed):
    cfg = get_config(guild.id)
    if cfg["log_channel_id"]:
        ch = guild.get_channel(cfg["log_channel_id"])
        if ch:
            try:
                await ch.send(embed=embed)
            except Exception:
                pass

async def notify_admins(embed: discord.Embed):
    for uid in NOTIFY_USER_IDS:
        try:
            user = await bot.fetch_user(uid)
            await user.send(embed=embed)
        except Exception:
            pass

async def notify_target_user(user: discord.User | discord.Member, embed: discord.Embed):
    try:
        await user.send(embed=embed)
    except Exception:
        pass

# ── Expiry checker ────────────────────────────────────────────────────────────
@tasks.loop(minutes=1)
async def check_expiry():
    now  = now_brt().isoformat()
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM cargos_setados
           WHERE removido = 0
             AND tempo_permanente = 0
             AND data_expiracao IS NOT NULL
             AND data_expiracao <= ?""",
        (now,)
    ).fetchall()
    conn.close()

    for row in rows:
        row   = dict(row)
        guild = bot.get_guild(row["guild_id"])
        if not guild:
            continue

        member        = guild.get_member(row["user_id"])
        role          = guild.get_role(row["cargo_id"])
        data_removido = now_brt().isoformat()

        if member and role:
            try:
                await member.remove_roles(role, reason="Tempo expirado - br! bot")
            except Exception:
                pass

        conn2 = get_db()
        conn2.execute(
            "UPDATE cargos_setados SET removido = 1, data_removido = ? WHERE id = ?",
            (data_removido, row["id"])
        )
        conn2.commit()
        conn2.close()

        servidor_id_val = row.get("servidor_id") or "Nao informado"

        embed = discord.Embed(
            title="Tempo de Cargo Expirado",
            color=discord.Color(0x000000),
            timestamp=now_brt()
        )
        embed.add_field(name="Usuario",        value=f"<@{row['user_id']}> (`{row['user_id']}`)",                     inline=False)
        embed.add_field(name="Nome",           value=str(member) if member else f"`{row['user_id']}`",                inline=True)
        embed.add_field(name="Cargo",          value=f"<@&{row['cargo_id']}>" if role else f"ID `{row['cargo_id']}`", inline=True)
        embed.add_field(name="Servidor Dono",  value=row["servidor_nome"],                                             inline=True)
        embed.add_field(name="ID do Servidor", value=servidor_id_val,                                                  inline=True)
        embed.add_field(name="Data Setado",    value=fmt_dt(row["data_setado"]),                                       inline=True)
        embed.add_field(name="Data Removido",  value=fmt_dt(data_removido),                                           inline=True)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.set_footer(text=f"Servidor: {guild.name}")

        await notify_admins(embed)

        target_user = member or (await bot.fetch_user(row["user_id"]) if row["user_id"] else None)
        if target_user:
            await notify_target_user(target_user, embed)

        if guild:
            await send_log(guild, embed)

        desativar_embed = discord.Embed(
            title="⚠️ Desativar Servidor",
            description="O tempo do cargo expirou. Por favor, **desative o servidor** abaixo:",
            color=discord.Color(0x000000),
            timestamp=now_brt()
        )
        desativar_embed.add_field(name="Nome do Servidor", value=row["servidor_nome"],                        inline=True)
        desativar_embed.add_field(name="ID do Servidor",   value=servidor_id_val,                             inline=True)
        desativar_embed.add_field(name="Usuario",          value=f"<@{row['user_id']}> (`{row['user_id']}`)", inline=False)
        if guild.icon:
            desativar_embed.set_thumbnail(url=guild.icon.url)
        desativar_embed.set_footer(text=f"Servidor: {guild.name}")

        await notify_admins(desativar_embed)

@check_expiry.before_loop
async def before_check():
    await bot.wait_until_ready()

# ── Views — Painel de Cargos ──────────────────────────────────────────────────
class PainelView(View):
    def __init__(self, ctx: commands.Context, target: discord.Member):
        super().__init__(timeout=30)
        self.ctx         = ctx
        self.target      = target
        self.cargo_id    = None
        self.servidor    = None
        self.servidor_id = None
        self.msg: discord.Message | None = None

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.msg:
            try:
                await self.msg.edit(view=self)
            except Exception:
                pass

    async def _try_finalizar(self, interaction: discord.Interaction):
        if self.cargo_id and self.servidor and self.servidor_id:
            await self._finalizar(interaction)

    async def _finalizar(self, interaction: discord.Interaction):
        guild    = self.ctx.guild
        now      = now_brt()

        # Verifica tempo especifico do cargo; padrao hardcoded de 30 dias se nao configurado
        role_t = get_role_tempo(guild.id, self.cargo_id)
        if role_t is None:
            role_t = 30

        permanente = (role_t == -1)
        td         = tempo_timedelta(role_t)
        expiracao  = (now + td) if td is not None else None
        t_str      = tempo_str(role_t)

        role = guild.get_role(self.cargo_id)
        if role:
            try:
                await self.target.add_roles(role, reason=f"Setado por {interaction.user} via br!painel")
            except Exception:
                pass

        conn = get_db()
        conn.execute("""
            INSERT INTO cargos_setados
                (guild_id, user_id, cargo_id, servidor_nome, servidor_id,
                 data_setado, data_expiracao, setado_por, tempo_permanente)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            guild.id, self.target.id, self.cargo_id,
            self.servidor, self.servidor_id, now.isoformat(),
            expiracao.isoformat() if expiracao else None,
            interaction.user.id,
            1 if permanente else 0
        ))
        conn.commit()
        conn.close()

        expira_valor = expiracao.strftime("%d/%m/%Y %H:%M") if expiracao else "Permanente"

        embed = make_base_embed(guild, "Cargo Setado", discord.Color(0x000000))
        embed.add_field(name="Usuario",        value=self.target.mention,  inline=True)
        embed.add_field(name="Nome",           value=str(self.target),     inline=True)
        embed.add_field(name="Cargo",          value=f"<@&{self.cargo_id}>", inline=True)
        embed.add_field(name="Servidor Dono",  value=self.servidor,        inline=True)
        embed.add_field(name="ID do Servidor", value=self.servidor_id,     inline=True)
        embed.add_field(name="Tempo",          value=t_str,                inline=True)
        embed.add_field(name="Data Setado",    value=now.strftime("%d/%m/%Y %H:%M"), inline=True)
        embed.add_field(name="Expira em",      value=expira_valor,         inline=True)
        embed.add_field(name="Setado por",     value=interaction.user.mention, inline=True)

        for item in self.children:
            item.disabled = True
        if self.msg:
            await self.msg.edit(embed=embed, view=self)

        await notify_admins(embed)

        dm_embed = make_base_embed(guild, "Cargo Temporario Recebido", discord.Color(0x000000))
        dm_embed.add_field(name="Cargo",          value=f"<@&{self.cargo_id}>",    inline=True)
        dm_embed.add_field(name="Servidor Dono",  value=self.servidor,              inline=True)
        dm_embed.add_field(name="ID do Servidor", value=self.servidor_id,           inline=True)
        dm_embed.add_field(name="Data Setado",    value=now.strftime("%d/%m/%Y %H:%M"), inline=True)
        dm_embed.add_field(name="Expira em",      value=expira_valor,               inline=True)
        dm_embed.add_field(name="Tempo",          value=t_str,                      inline=True)
        dm_embed.add_field(name="Setado por",     value=str(interaction.user),      inline=True)
        await notify_target_user(self.target, dm_embed)

        await send_log(guild, embed)

    @discord.ui.button(label="Adicionar Cargo", style=discord.ButtonStyle.secondary)
    async def btn_cargo(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("Apenas quem abriu o painel pode usar.", ephemeral=True)
            return

        roles = [
            r for r in interaction.guild.roles
            if r != interaction.guild.default_role and not r.managed
        ][:25]

        if not roles:
            await interaction.response.send_message("Nenhum cargo disponivel.", ephemeral=True)
            return

        options = [
            discord.SelectOption(label=r.name[:100], value=str(r.id))
            for r in roles
        ]

        select_view = CargoSelectView(self, options)
        await interaction.response.send_message(
            "Selecione o cargo:", view=select_view, ephemeral=True
        )

    @discord.ui.button(label="Adicionar Servidor", style=discord.ButtonStyle.secondary)
    async def btn_servidor(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("Apenas quem abriu o painel pode usar.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Digite o **nome do servidor** no chat agora (voce tem 60 segundos):",
            ephemeral=True
        )

        def check(m: discord.Message):
            return m.author == self.ctx.author and m.channel == self.ctx.channel

        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
            self.servidor = msg.content.strip()
            await msg.delete()
            await interaction.followup.send(f"Servidor **{self.servidor}** salvo!", ephemeral=True)
            await self._try_finalizar(interaction)
        except asyncio.TimeoutError:
            await interaction.followup.send("Tempo esgotado para digitar o servidor.", ephemeral=True)

    @discord.ui.button(label="Adicionar ID do Servidor", style=discord.ButtonStyle.secondary)
    async def btn_servidor_id(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("Apenas quem abriu o painel pode usar.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Digite o **ID do servidor** no chat agora (voce tem 60 segundos):",
            ephemeral=True
        )

        def check(m: discord.Message):
            return m.author == self.ctx.author and m.channel == self.ctx.channel

        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
            self.servidor_id = msg.content.strip()
            await msg.delete()
            await interaction.followup.send(f"ID do Servidor **{self.servidor_id}** salvo!", ephemeral=True)
            await self._try_finalizar(interaction)
        except asyncio.TimeoutError:
            await interaction.followup.send("Tempo esgotado para digitar o ID do servidor.", ephemeral=True)


class CargoSelectView(View):
    def __init__(self, painel_view: PainelView, options: list[discord.SelectOption]):
        super().__init__(timeout=30)
        self.painel_view = painel_view

        select = Select(placeholder="Escolha um cargo...", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        self.painel_view.cargo_id = int(interaction.data["values"][0])
        role = interaction.guild.get_role(self.painel_view.cargo_id)

        role_t = get_role_tempo(interaction.guild.id, self.painel_view.cargo_id)
        if role_t is None:
            tempo_info = "⚠️ Sem tempo configurado (padrao: 30 dias)"
        else:
            tempo_info = f"**{tempo_str(role_t)}**"

        await interaction.response.send_message(
            f"Cargo **{role.name if role else self.painel_view.cargo_id}** selecionado!\n"
            f"Tempo: {tempo_info}",
            ephemeral=True
        )
        await self.painel_view._try_finalizar(interaction)


# ── View — Seletor de Tempo (por cargo ou global) ─────────────────────────────
class TempoView(View):
    def __init__(self, ctx: commands.Context, role: discord.Role | None = None):
        super().__init__(timeout=30)
        self.ctx  = ctx
        self.role = role  # None = configuração global

    @discord.ui.select(
        placeholder="Selecione o tempo...",
        options=[
            discord.SelectOption(label="30 segundos",  value="0",   description="Cargo expira em 30 segundos (teste)"),
            discord.SelectOption(label="30 dias",      value="30",  description="Cargo expira em 30 dias"),
            discord.SelectOption(label="60 dias",      value="60",  description="Cargo expira em 60 dias"),
            discord.SelectOption(label="90 dias",      value="90",  description="Cargo expira em 90 dias"),
            discord.SelectOption(label="Permanente",   value="-1",  description="Cargo nunca expira (permanente)"),
        ]
    )
    async def select_tempo(self, interaction: discord.Interaction, select: Select):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("Sem permissao.", ephemeral=True)
            return

        dias = int(select.values[0])

        if self.role is not None:
            # Salva tempo para o cargo específico
            set_role_tempo(self.ctx.guild.id, self.role.id, dias)
            titulo = f"Tempo Configurado — {self.role.name}"
            desc   = f"O cargo {self.role.mention} agora usa **{tempo_str(dias)}** como tempo de expiração."
        else:
            # Salva tempo global
            upsert_config(self.ctx.guild.id, tempo_dias=dias)
            titulo = "Tempo Global Configurado"
            desc   = f"O tempo padrão de cargo foi definido para **{tempo_str(dias)}**."

        embed = make_base_embed(self.ctx.guild, titulo, discord.Color(0x000000))
        embed.description = desc

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        await asyncio.sleep(5)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
        self.stop()


# ── Modals — EmbedEnv ─────────────────────────────────────────────────────────
class ModalTitulo(Modal, title="Definir Titulo"):
    campo = TextInput(
        label="Titulo da embed",
        placeholder="Digite o titulo...",
        max_length=256,
        required=True
    )

    def __init__(self, env_view):
        super().__init__()
        self.env_view = env_view

    async def on_submit(self, interaction: discord.Interaction):
        self.env_view.state["titulo"] = self.campo.value.strip()
        await interaction.response.send_message(
            f"Titulo definido: **{self.campo.value.strip()}**", ephemeral=True
        )
        await self.env_view.atualizar_painel()


class ModalBanner(Modal, title="Definir Banner"):
    campo = TextInput(
        label="URL da imagem do banner",
        placeholder="https://i.imgur.com/exemplo.png",
        max_length=500,
        required=True
    )

    def __init__(self, env_view):
        super().__init__()
        self.env_view = env_view

    async def on_submit(self, interaction: discord.Interaction):
        self.env_view.state["banner"] = self.campo.value.strip()
        await interaction.response.send_message("Banner salvo!", ephemeral=True)
        await self.env_view.atualizar_painel()


class ModalLogo(Modal, title="Definir Logo"):
    campo = TextInput(
        label="URL da imagem da logo",
        placeholder="https://i.imgur.com/exemplo.png",
        max_length=500,
        required=True
    )

    def __init__(self, env_view):
        super().__init__()
        self.env_view = env_view

    async def on_submit(self, interaction: discord.Interaction):
        self.env_view.state["logo"] = self.campo.value.strip()
        await interaction.response.send_message("Logo salva!", ephemeral=True)
        await self.env_view.atualizar_painel()


class ModalCor(Modal, title="Definir Cor"):
    campo = TextInput(
        label="Cor em hexadecimal",
        placeholder="#FF0000",
        min_length=4,
        max_length=7,
        required=True
    )

    def __init__(self, env_view):
        super().__init__()
        self.env_view = env_view

    async def on_submit(self, interaction: discord.Interaction):
        valor = self.campo.value.strip().lstrip("#")
        try:
            int(valor, 16)
            self.env_view.state["cor"] = f"#{valor.upper()}"
            await interaction.response.send_message(
                f"Cor definida: **#{valor.upper()}**", ephemeral=True
            )
            await self.env_view.atualizar_painel()
        except ValueError:
            await interaction.response.send_message(
                "Cor invalida. Use formato hex, ex: `#FF0000`", ephemeral=True
            )


class ModalCanal(Modal, title="Definir Canal"):
    campo = TextInput(
        label="ID ou mencao do canal",
        placeholder="123456789012345678",
        max_length=100,
        required=True
    )

    def __init__(self, env_view):
        super().__init__()
        self.env_view = env_view

    async def on_submit(self, interaction: discord.Interaction):
        valor = self.campo.value.strip().strip("<>#")
        try:
            canal_id = int(valor)
            canal    = interaction.guild.get_channel(canal_id)
            if canal and isinstance(canal, discord.TextChannel):
                self.env_view.state["canal_id"] = canal.id
                await interaction.response.send_message(
                    f"Canal definido: {canal.mention}", ephemeral=True
                )
                await self.env_view.atualizar_painel()
            else:
                await interaction.response.send_message(
                    "Canal nao encontrado ou nao e um canal de texto.", ephemeral=True
                )
        except ValueError:
            await interaction.response.send_message(
                "ID invalido. Coloque apenas os numeros do ID do canal.", ephemeral=True
            )


# ── View — EmbedEnv ───────────────────────────────────────────────────────────
class EmbedEnvView(View):
    def __init__(self, ctx: commands.Context):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.msg: discord.Message | None = None
        self.state = {
            "titulo":   None,
            "banner":   None,
            "logo":     None,
            "cor":      None,
            "canal_id": None,
        }

    def _nd(self, valor):
        return valor if valor else "Nao definido"

    def build_painel_embed(self) -> discord.Embed:
        canal = self.ctx.guild.get_channel(self.state["canal_id"]) if self.state["canal_id"] else None

        embed = discord.Embed(
            title="Painel de Embed",
            color=discord.Color(0x000000),
            timestamp=now_brt()
        )
        embed.add_field(name="Titulo",  value=self._nd(self.state["titulo"]),  inline=True)
        embed.add_field(name="Cor",     value=self._nd(self.state["cor"]),      inline=True)
        embed.add_field(name="Canal",   value=canal.mention if canal else "Nao definido", inline=True)
        embed.add_field(name="Banner",  value=self._nd(self.state["banner"]),   inline=False)
        embed.add_field(name="Logo",    value=self._nd(self.state["logo"]),     inline=False)
        embed.set_footer(text=self.ctx.guild.name)
        return embed

    async def atualizar_painel(self):
        if self.msg:
            try:
                await self.msg.edit(embed=self.build_painel_embed(), view=self)
            except Exception:
                pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.msg:
            try:
                await self.msg.edit(view=self)
            except Exception:
                pass

    def _check_autor(self, interaction: discord.Interaction) -> bool:
        return interaction.user == self.ctx.author

    @discord.ui.button(label="Titulo", style=discord.ButtonStyle.secondary, row=0)
    async def btn_titulo(self, interaction: discord.Interaction, button: Button):
        if not self._check_autor(interaction):
            await interaction.response.send_message("Apenas quem abriu o painel pode usar.", ephemeral=True)
            return
        await interaction.response.send_modal(ModalTitulo(self))

    @discord.ui.button(label="Banner", style=discord.ButtonStyle.secondary, row=0)
    async def btn_banner(self, interaction: discord.Interaction, button: Button):
        if not self._check_autor(interaction):
            await interaction.response.send_message("Apenas quem abriu o painel pode usar.", ephemeral=True)
            return
        await interaction.response.send_modal(ModalBanner(self))

    @discord.ui.button(label="Logo", style=discord.ButtonStyle.secondary, row=0)
    async def btn_logo(self, interaction: discord.Interaction, button: Button):
        if not self._check_autor(interaction):
            await interaction.response.send_message("Apenas quem abriu o painel pode usar.", ephemeral=True)
            return
        await interaction.response.send_modal(ModalLogo(self))

    @discord.ui.button(label="Cor", style=discord.ButtonStyle.secondary, row=1)
    async def btn_cor(self, interaction: discord.Interaction, button: Button):
        if not self._check_autor(interaction):
            await interaction.response.send_message("Apenas quem abriu o painel pode usar.", ephemeral=True)
            return
        await interaction.response.send_modal(ModalCor(self))

    @discord.ui.button(label="Canal", style=discord.ButtonStyle.secondary, row=1)
    async def btn_canal(self, interaction: discord.Interaction, button: Button):
        if not self._check_autor(interaction):
            await interaction.response.send_message("Apenas quem abriu o painel pode usar.", ephemeral=True)
            return
        await interaction.response.send_modal(ModalCanal(self))

    @discord.ui.button(label="Enviar", style=discord.ButtonStyle.secondary, row=1)
    async def btn_enviar(self, interaction: discord.Interaction, button: Button):
        if not self._check_autor(interaction):
            await interaction.response.send_message("Apenas quem abriu o painel pode usar.", ephemeral=True)
            return

        if not self.state["titulo"]:
            await interaction.response.send_message("Defina o titulo antes de enviar.", ephemeral=True)
            return

        if not self.state["canal_id"]:
            await interaction.response.send_message("Defina o canal antes de enviar.", ephemeral=True)
            return

        canal = interaction.guild.get_channel(self.state["canal_id"])
        if not canal:
            await interaction.response.send_message("Canal nao encontrado.", ephemeral=True)
            return

        cor_int = 0x000000
        if self.state["cor"]:
            try:
                cor_int = int(self.state["cor"].lstrip("#"), 16)
            except Exception:
                pass

        embed_final = discord.Embed(
            title=self.state["titulo"],
            color=discord.Color(cor_int),
            timestamp=now_brt()
        )

        if self.state["banner"]:
            embed_final.set_image(url=self.state["banner"])

        if self.state["logo"]:
            embed_final.set_thumbnail(url=self.state["logo"])

        embed_final.set_footer(text=interaction.guild.name)

        try:
            await canal.send(embed=embed_final)
            await interaction.response.send_message(
                f"Embed enviada para {canal.mention}!", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Sem permissao para enviar mensagens nesse canal.", ephemeral=True
            )


# ── Checks ────────────────────────────────────────────────────────────────────
def is_owner():
    async def predicate(ctx: commands.Context):
        return ctx.author.id == ctx.guild.owner_id
    return commands.check(predicate)

def is_staff():
    async def predicate(ctx: commands.Context):
        if ctx.author.id == ctx.guild.owner_id:
            return True
        cfg = get_config(ctx.guild.id)
        if cfg["staff_role_id"]:
            role = ctx.guild.get_role(cfg["staff_role_id"])
            if role and role in ctx.author.roles:
                return True
        raise commands.CheckFailure("Voce nao tem permissao para usar este comando.")
    return commands.check(predicate)


# ── Commands ──────────────────────────────────────────────────────────────────

@bot.command(name="painel")
@is_staff()
async def cmd_painel(ctx: commands.Context, target: discord.Member = None):
    if target is None:
        embed = make_base_embed(ctx.guild, "Erro", discord.Color(0x000000))
        embed.description = "Mencione um usuario: `br!painel @usuario`"
        await ctx.send(embed=embed)
        return

    embed = make_base_embed(ctx.guild, "Painel de Setagem", discord.Color(0x000000))
    embed.description = (
        f"Configurando cargo para {target.mention}\n\n"
        "Use os botoes abaixo para configurar o **cargo**, o **servidor** e o **ID do servidor**.\n"
        "O painel fecha automaticamente em **30 segundos**."
    )

    view = PainelView(ctx, target)
    msg  = await ctx.send(embed=embed, view=view)
    view.msg = msg
    await view.wait()

    if not (view.cargo_id and view.servidor and view.servidor_id):
        for item in view.children:
            item.disabled = True
        expired_embed = make_base_embed(ctx.guild, "Painel Expirado", discord.Color(0x000000))
        expired_embed.description = "O painel expirou sem configuracao completa."
        try:
            await msg.edit(embed=expired_embed, view=view)
        except Exception:
            pass
        await asyncio.sleep(3)
        try:
            await msg.delete()
        except Exception:
            pass


@bot.command(name="perfil")
@is_staff()
async def cmd_perfil(ctx: commands.Context, target: discord.Member = None):
    """Mostra o painel com todos os cargos ativos e o tempo restante de cada um."""
    if target is None:
        embed = make_base_embed(ctx.guild, "Erro", discord.Color(0x000000))
        embed.description = "Mencione um usuario: `br!perfil @usuario`"
        await ctx.send(embed=embed)
        return

    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM cargos_setados
           WHERE guild_id = ? AND user_id = ? AND removido = 0
           ORDER BY data_setado DESC""",
        (ctx.guild.id, target.id)
    ).fetchall()
    conn.close()

    embed = make_base_embed(ctx.guild, f"Perfil de Cargos — {target.display_name}", discord.Color(0x000000))

    if target.avatar:
        embed.set_thumbnail(url=target.display_avatar.url)

    embed.description = f"Usuario: {target.mention} (`{target.id}`)"

    if not rows:
        embed.add_field(
            name="Nenhum cargo ativo",
            value="Este usuario nao possui cargos temporarios ativos no momento.",
            inline=False
        )
    else:
        for row in rows:
            row  = dict(row)
            role = ctx.guild.get_role(row["cargo_id"])
            nome_cargo = role.mention if role else f"Cargo removido (`{row['cargo_id']}`)"

            permanente = row.get("tempo_permanente", 0)
            restante   = tempo_restante_str(row.get("data_expiracao"), permanente)
            expiracao  = "Permanente" if permanente else fmt_dt(row.get("data_expiracao"))

            setado_por_user = ctx.guild.get_member(row["setado_por"])
            setado_por_str  = setado_por_user.mention if setado_por_user else f"`{row['setado_por']}`"

            embed.add_field(
                name=f"╔ {role.name if role else 'Cargo Removido'}",
                value=(
                    f"**Cargo:** {nome_cargo}\n"
                    f"**Servidor:** {row['servidor_nome']} (`{row.get('servidor_id') or 'N/A'}`)\n"
                    f"**Setado em:** {fmt_dt(row['data_setado'])}\n"
                    f"**Expira:** {expiracao}\n"
                    f"**Tempo restante:** {restante}\n"
                    f"**Setado por:** {setado_por_str}"
                ),
                inline=False
            )

    embed.set_footer(text=f"{ctx.guild.name} • {len(rows)} cargo(s) ativo(s)")
    await ctx.send(embed=embed)


@bot.command(name="setcargo")
@is_owner()
async def cmd_setcargo(ctx: commands.Context, role: discord.Role = None):
    if role is None:
        embed = make_base_embed(ctx.guild, "Erro", discord.Color(0x000000))
        embed.description = "Mencione um cargo: `br!setcargo @cargo`"
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        await msg.delete()
        return

    upsert_config(ctx.guild.id, staff_role_id=role.id)

    embed = make_base_embed(ctx.guild, "Cargo Staff Configurado", discord.Color(0x000000))
    embed.description = f"O cargo {role.mention} agora pode usar `br!painel`."
    msg = await ctx.send(embed=embed)
    await asyncio.sleep(5)
    try:
        await msg.delete()
    except Exception:
        pass


@bot.command(name="settempo")
@is_owner()
async def cmd_settempo(ctx: commands.Context, role: discord.Role = None):
    """Define o tempo de expiracao para um cargo especifico."""
    if role is None:
        embed = make_base_embed(ctx.guild, "Erro", discord.Color(0x000000))
        embed.description = f"Mencione um cargo: `{PREFIX}settempo @cargo`"
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    embed = make_base_embed(ctx.guild, f"Configurar Tempo — {role.name}", discord.Color(0x000000))
    embed.description = (
        f"Selecione o tempo de expiracao para o cargo {role.mention}.\n"
        f"Este tempo sera usado sempre que esse cargo for setado via `{PREFIX}painel`."
    )

    view = TempoView(ctx, role=role)
    msg  = await ctx.send(embed=embed, view=view)
    await view.wait()

    await asyncio.sleep(5)
    try:
        await msg.delete()
    except Exception:
        pass


@bot.command(name="tempos")
@is_owner()
async def cmd_tempos(ctx: commands.Context):
    """Mostra o painel com todos os cargos e seus tempos configurados."""
    conn = get_db()
    rows = conn.execute(
        "SELECT cargo_id, tempo_dias FROM role_tempo WHERE guild_id = ? ORDER BY tempo_dias",
        (ctx.guild.id,)
    ).fetchall()
    conn.close()

    embed = make_base_embed(ctx.guild, "Configuracoes de Tempo por Cargo", discord.Color(0x000000))

    if not rows:
        embed.description = (
            "Nenhum cargo com tempo configurado.\n"
            f"Use `{PREFIX}settempo @cargo` para definir o tempo de um cargo."
        )
    else:
        linhas = []
        for row in rows:
            role = ctx.guild.get_role(row["cargo_id"])
            nome = role.mention if role else f"~~Cargo removido~~ (`{row['cargo_id']}`)"
            t    = tempo_str(row["tempo_dias"])
            linhas.append(f"{nome} — **{t}**")

        embed.description = "\n".join(linhas)
        embed.set_footer(text=f"{ctx.guild.name} • {len(rows)} cargo(s) configurado(s)")

    await ctx.send(embed=embed)


@bot.command(name="log")
@is_owner()
async def cmd_log(ctx: commands.Context, channel: discord.TextChannel = None):
    target_channel = channel or ctx.channel
    upsert_config(ctx.guild.id, log_channel_id=target_channel.id)

    embed = make_base_embed(ctx.guild, "Canal de Log Configurado", discord.Color(0x000000))
    embed.description = f"O canal {target_channel.mention} agora recebera todos os logs de cargos."
    await ctx.send(embed=embed)


@bot.command(name="embedenv")
@is_owner()
async def cmd_embedenv(ctx: commands.Context):
    view = EmbedEnvView(ctx)
    msg  = await ctx.send(embed=view.build_painel_embed(), view=view)
    view.msg = msg
    await view.wait()


@bot.command(name="start")
@is_owner()
async def cmd_start(ctx: commands.Context):
    cfg        = get_config(ctx.guild.id)
    staff_role = ctx.guild.get_role(cfg["staff_role_id"]) if cfg["staff_role_id"] else None
    log_ch     = ctx.guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
    tempo      = cfg["tempo_dias"]

    def status(valor):
        return "Configurado" if valor else "Pendente"

    embed = make_base_embed(ctx.guild, "Configuracao Inicial - br! Bot", discord.Color(0x000000))
    embed.description = (
        "Bem-vindo! Siga os passos abaixo para configurar o bot no servidor.\n"
        "Use cada comando na ordem indicada."
    )

    embed.add_field(
        name="Passo 1 — Cargo Staff",
        value=(
            f"`{PREFIX}setcargo @cargo`\n"
            f"Define qual cargo pode usar o `{PREFIX}painel`.\n"
            f"Situacao: **{status(staff_role)}**"
            + (f" ({staff_role.mention})" if staff_role else "")
        ),
        inline=False
    )

    embed.add_field(
        name="Passo 2 — Tempo por Cargo",
        value=(
            f"`{PREFIX}settempo @cargo` — define o tempo de expiracao de um cargo\n"
            f"Opcoes: 30s, 30, 60, 90 dias ou **Permanente**.\n"
            f"`{PREFIX}tempos` — ve todos os cargos e seus tempos configurados"
        ),
        inline=False
    )

    embed.add_field(
        name="Passo 3 — Canal de Log",
        value=(
            f"`{PREFIX}log #canal`\n"
            f"Define o canal que recebe os registros de cargos setados e removidos.\n"
            f"Situacao: **{status(log_ch)}**"
            + (f" ({log_ch.mention})" if log_ch else "")
        ),
        inline=False
    )

    embed.add_field(
        name="Pronto para usar",
        value=(
            f"`{PREFIX}painel @usuario` — Abre o painel para setar cargo em um usuario\n"
            f"`{PREFIX}perfil @usuario` — Mostra os cargos ativos e o tempo restante de cada um"
        ),
        inline=False
    )

    await ctx.send(embed=embed)


@bot.command(name="help")
async def cmd_help(ctx: commands.Context):
    cfg        = get_config(ctx.guild.id)
    staff_role = ctx.guild.get_role(cfg["staff_role_id"]) if cfg["staff_role_id"] else None
    log_ch     = ctx.guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None

    embed = make_base_embed(ctx.guild, "Comandos - br! Bot", discord.Color(0x000000))
    embed.description = f"Prefixo: `{PREFIX}`"

    embed.add_field(
        name="Comandos Gerais",
        value=f"`{PREFIX}help` — Mostra esta lista de comandos",
        inline=False
    )

    embed.add_field(
        name="Comandos Staff",
        value=(
            f"`{PREFIX}painel @usuario` — Abre painel para setar cargo e servidor na pessoa\n"
            f"  - Botao **Adicionar Cargo**: escolhe o cargo pelo menu (mostra o tempo do cargo)\n"
            f"  - Botao **Adicionar Servidor**: digita o nome do servidor no chat\n"
            f"  - Botao **Adicionar ID do Servidor**: digita o ID do servidor no chat\n"
            f"  - Painel fecha em 30 segundos\n\n"
            f"`{PREFIX}perfil @usuario` — Painel com todos os cargos ativos do usuario e tempo restante"
        ),
        inline=False
    )

    embed.add_field(
        name="Comandos Dono",
        value=(
            f"`{PREFIX}start` — Guia de configuracao inicial do bot\n"
            f"`{PREFIX}setcargo @cargo` — Define qual cargo pode usar o painel\n"
            f"`{PREFIX}settempo @cargo` — Define o tempo de expiracao de um cargo\n"
            f"  - Opcoes: 30s / 30 / 60 / 90 dias / **Permanente**\n"
            f"`{PREFIX}tempos` — Mostra todos os cargos e seus tempos configurados\n"
            f"`{PREFIX}log [#canal]` — Define o canal que recebe os logs de cargos\n"
            f"`{PREFIX}embedenv` — Abre painel para criar e enviar uma embed personalizada"
        ),
        inline=False
    )

    embed.add_field(
        name="Configuracao Atual",
        value=(
            f"Cargo staff: {staff_role.mention if staff_role else 'Nao configurado'}\n"
            f"Canal de log: {log_ch.mention if log_ch else 'Nao configurado'}"
        ),
        inline=False
    )

    await ctx.send(embed=embed)


# ── Error handling ────────────────────────────────────────────────────────────
@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CheckFailure):
        embed = make_base_embed(ctx.guild, "Sem Permissao", discord.Color(0x000000))
        embed.description = str(error) if str(error) else "Voce nao tem permissao para usar este comando."
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except Exception:
            pass
    elif isinstance(error, commands.MemberNotFound):
        embed = make_base_embed(ctx.guild, "Usuario Nao Encontrado", discord.Color(0x000000))
        embed.description = "Nao consegui encontrar esse usuario."
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except Exception:
            pass
    elif isinstance(error, commands.RoleNotFound):
        embed = make_base_embed(ctx.guild, "Cargo Nao Encontrado", discord.Color(0x000000))
        embed.description = "Nao consegui encontrar esse cargo."
        msg = await ctx.send(embed=embed)
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except Exception:
            pass
    else:
        raise error


# ── Events ────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    init_db()
    check_expiry.start()
    print(f"Bot online como {bot.user} (ID: {bot.user.id})")
    print(f"Prefixo: {PREFIX}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="br!help | Cargos Temporarios"
        )
    )


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        print("DISCORD_TOKEN nao definido. Configure a variavel de ambiente.")
        exit(1)
    bot.run.(TOKEN)
