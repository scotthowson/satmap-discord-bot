#!/usr/bin/env python3
"""The SatMap bot for Discord: slash commands answered from SatMap's own pages, a live status board, news and alerts.

    python3 bot.py            run it
    python3 bot.py --check    read the settings, ask SatMap once, build every answer, and stop: no login, no token needed

Settings come from config.json beside this file (or --config <file>) and from the environment, which wins: that is
how the container is set up (DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, SATMAP_URL, ...; README.md lists them all).
Everything it says comes from satmap_data.py.  It reads SatMap and writes to Discord; it never sends anything to the game.
"""
import asyncio, json, logging, os, sys, time, typing

import discord
from discord import app_commands
from discord.ext import tasks

import satmap_data as data

HERE = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger('satmap-bot')

DEFAULTS = {
    'token': '', 'tokenFile': '',
    'satmap': 'http://127.0.0.1:8765', 'timeoutSeconds': 6.0,
    'guild': 0, 'commandChannels': [], 'allowRoles': [], 'private': False, 'mapUrl': '', 'badges': True,
    'presence': True, 'presenceSeconds': 60,
    'board': {'channel': 0, 'seconds': 60},
    'news': {'channel': 0, 'joins': True, 'server': True},
    'alerts': {'channel': 0, 'minSeverity': 2, 'cleared': True, 'mention': '', 'mentionSeverity': 3, 'regroupMinutes': 10},
}


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULTS))
    given = json.load(open(path, encoding='utf-8-sig'))
    for key, value in given.items():
        if key.startswith('_'):
            continue
        if key not in cfg:
            log.warning('config: "%s" is not a setting this bot knows; ignored', key)
        elif isinstance(cfg[key], dict):
            unknown = sorted(set(value) - set(cfg[key]))
            if unknown:
                log.warning('config: "%s" has settings this bot does not know: %s', key, ', '.join(unknown))
            cfg[key].update({k: v for k, v in value.items() if k in cfg[key]})
        else:
            cfg[key] = value
    for key in ('guild',):
        cfg[key] = int(cfg[key] or 0)
    for section in ('board', 'news', 'alerts'):
        cfg[section]['channel'] = int(cfg[section]['channel'] or 0)
    cfg['commandChannels'] = [int(c) for c in cfg['commandChannels']]
    cfg['allowRoles'] = [int(r) for r in cfg['allowRoles']]
    return cfg


def ids(text):
    return [int(part) for part in str(text).replace(';', ',').split(',') if part.strip().isdigit()]

def truth(text):
    return str(text).strip().lower() in ('1', 'true', 'yes', 'on')

# environment variable -> where it goes in the settings, and how it is read
ENVIRONMENT = [
    ('DISCORD_GUILD_ID', ('guild',), int), ('DISCORD_CHANNEL_IDS', ('commandChannels',), ids), ('DISCORD_ROLE_IDS', ('allowRoles',), ids),
    ('SATMAP_URL', ('satmap',), str), ('SATMAP_MAP_URL', ('mapUrl',), str), ('SATMAP_TIMEOUT_SECONDS', ('timeoutSeconds',), float),
    ('SATMAP_PRIVATE_REPLIES', ('private',), truth), ('SATMAP_BADGES', ('badges',), truth),
    ('SATMAP_PRESENCE', ('presence',), truth), ('SATMAP_PRESENCE_SECONDS', ('presenceSeconds',), int),
    ('SATMAP_BOARD_CHANNEL_ID', ('board', 'channel'), int), ('SATMAP_BOARD_SECONDS', ('board', 'seconds'), int),
    ('SATMAP_NEWS_CHANNEL_ID', ('news', 'channel'), int), ('SATMAP_NEWS_JOINS', ('news', 'joins'), truth), ('SATMAP_NEWS_SERVER', ('news', 'server'), truth),
    ('SATMAP_ALERTS_CHANNEL_ID', ('alerts', 'channel'), int), ('SATMAP_ALERTS_MIN_SEVERITY', ('alerts', 'minSeverity'), int),
    ('SATMAP_ALERTS_CLEARED', ('alerts', 'cleared'), truth), ('SATMAP_ALERTS_REGROUP_MINUTES', ('alerts', 'regroupMinutes'), float), ('SATMAP_ALERTS_MENTION', ('alerts', 'mention'), str), ('SATMAP_ALERTS_MENTION_SEVERITY', ('alerts', 'mentionSeverity'), int),
]

def apply_environment(cfg):
    """settings given in the environment win over the file; an empty variable changes nothing"""
    used = []
    for name, path, read in ENVIRONMENT:
        text = os.environ.get(name, '').strip()
        if not text:
            continue
        try:
            value = read(text)
        except ValueError:
            log.warning('%s="%s" is not a value this setting takes; ignored', name, text)
            continue
        target = cfg
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        used.append(name)
    return used


def read_token(cfg):
    """DISCORD_TOKEN in the environment, else the file named by tokenFile, else "token" in the settings"""
    token = (os.environ.get('DISCORD_BOT_TOKEN') or os.environ.get('DISCORD_TOKEN') or '').strip()
    if not token and cfg['tokenFile']:
        token = open(os.path.expanduser(cfg['tokenFile']), encoding='utf-8').read().strip()
    return token or str(cfg['token']).strip()


class Memory:
    """what must survive a restart: the board's message, which alert is which message, who was online"""

    def __init__(self, path):
        self.path = path
        try:
            self.data = json.load(open(path, encoding='utf-8'))
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        if self.data.get(key) != value:
            self.data[key] = value
            with open(self.path + '.new', 'w', encoding='utf-8') as out:
                json.dump(self.data, out)
            os.replace(self.path + '.new', self.path)


def memory_path(beside):
    """where memory.json goes: SATMAP_DATA_DIR, else beside the settings, else the temporary folder (a read-only container)"""
    import tempfile
    for folder in (os.environ.get('SATMAP_DATA_DIR', ''), beside, tempfile.gettempdir()):
        if folder and os.path.isdir(folder) and os.access(folder, os.W_OK):
            return os.path.join(folder, 'memory.json')
    return os.path.join(tempfile.gettempdir(), 'memory.json')


def render(card, badges=True, thumbnail=True):
    """a card from satmap_data as a Discord embed, with its badge as the small picture"""
    embed = discord.Embed(title=card['title'][:256], description='\n'.join(card['lines'])[:4096] or None, colour=card['colour'])
    for name, value, inline in card['fields'][:25]:
        embed.add_field(name=name[:256], value=value[:1024], inline=inline)
    if card['footer']:
        embed.set_footer(text=card['footer'][:2048])
    embed.timestamp = discord.utils.utcnow()
    files = []
    face = os.path.join(HERE, 'art', 'satmap-96.png')
    if badges and os.path.isfile(face):                              # the small picture and the name at the top of every message
        files.append(discord.File(face, filename='satmap.png'))
        embed.set_author(name='SatMap', icon_url='attachment://satmap.png')
    else:
        embed.set_author(name='SatMap')
    path = os.path.join(HERE, 'art', 'badges', card['badge'] + '.png')
    if badges and thumbnail and os.path.isfile(path):
        files.append(discord.File(path, filename=card['badge'] + '.png'))
        embed.set_thumbnail(url='attachment://%s.png' % card['badge'])
    return embed, files


class SatMapBot(discord.Client):
    def __init__(self, cfg, memory):
        super().__init__(intents=discord.Intents(guilds=True))
        self.cfg, self.memory = cfg, memory
        self.sm = data.SatMap(cfg['satmap'], timeout=float(cfg['timeoutSeconds']))
        self.tree = app_commands.CommandTree(self)
        self.first_watch = True
        self.board_looked = False
        self.bad_channels = set()
        for name, (_, about, argument) in data.COMMANDS.items():
            self.tree.add_command(self.make_command(name, about, argument))

    # -- commands ---------------------------------------------------------------------------------------------------
    def make_command(self, name, about, argument):
        bot = self
        if not argument:
            async def plain(interaction: discord.Interaction):
                await bot.reply(interaction, name, None)
            return app_commands.Command(name=name, description=about[:100], callback=plain)

        async def named(interaction: discord.Interaction, arg: typing.Optional[str] = None):
            await bot.reply(interaction, name, arg)
        named = app_commands.rename(arg=argument)(named)
        named = app_commands.describe(arg='Leave empty for the overview, or name one %s' % argument)(named)
        command = app_commands.Command(name=name, description=about[:100], callback=named)

        @command.autocomplete('arg')
        async def suggest(interaction: discord.Interaction, current: str):
            names = await asyncio.to_thread(data.choices, bot.sm, name)
            current = current.lower()
            first = [n for n in names if n.lower().startswith(current)]
            return [app_commands.Choice(name=n[:100], value=n[:100]) for n in (first + [n for n in names if current in n.lower() and n not in first])[:25]]
        return command

    def allowed(self, interaction):
        roles = self.cfg['allowRoles']
        if not roles:
            return True
        return any(role.id in roles for role in getattr(interaction.user, 'roles', []))

    async def reply(self, interaction, command, arg):
        if not self.allowed(interaction):
            await interaction.response.send_message('These commands are for the pack\'s players.', ephemeral=True)
            return
        channels = self.cfg['commandChannels']
        private = bool(self.cfg['private']) or bool(channels and interaction.channel_id not in channels)
        await interaction.response.defer(ephemeral=private, thinking=True)
        card = await asyncio.to_thread(data.answer, self.sm, command, arg)
        embed, files = render(card, self.cfg['badges'])
        await interaction.followup.send(embed=embed, files=files, view=self.map_button() or discord.utils.MISSING, ephemeral=private)

    def map_button(self):
        if not self.cfg['mapUrl']:
            return None
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label='Open the map', url=self.cfg['mapUrl']))
        return view

    # -- start ------------------------------------------------------------------------------------------------------
    async def setup_hook(self):
        if self.cfg['guild']:
            guild = discord.Object(id=self.cfg['guild'])
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)             # in one server: there at once
            self.tree.clear_commands(guild=None)
            await self.tree.sync()                                 # and not a second time as global commands
            log.info('%d commands set in server %d', len(synced), self.cfg['guild'])
        else:
            synced = await self.tree.sync()                        # everywhere the bot is: Discord may take up to an hour
            log.info('%d commands set globally (name a "guild" in the settings to have them at once)', len(synced))
        self.heartbeat.start()
        if self.cfg['presence']:
            self.presence_loop.change_interval(seconds=max(20, int(self.cfg['presenceSeconds'])))
            self.presence_loop.start()
        if self.cfg['board']['channel']:
            self.board_loop.change_interval(seconds=max(20, int(self.cfg['board']['seconds'])))
            self.board_loop.start()
        if self.cfg['news']['channel'] or self.cfg['alerts']['channel']:
            self.watch_loop.start()

    async def on_ready(self):
        log.info('logged in as %s, reading %s', self.user, self.cfg['satmap'])

    async def channel(self, channel_id):
        """the channel for the board, the news or the alerts; a number that is no channel is said once and then left alone"""
        if not channel_id or channel_id in self.bad_channels:
            return None
        found = self.get_channel(channel_id)
        if found is None:
            try:
                found = await self.fetch_channel(channel_id)
            except discord.NotFound:
                self.bad_channels.add(channel_id)
                log.warning('%d is not a channel Discord knows (it may be the ID of a user, the bot, a message or the server). Right-click the channel itself and choose Copy Channel ID, '
                            'set it and restart the bot. What was to be posted there stays off until then; the commands work.', channel_id)
            except discord.Forbidden:
                self.bad_channels.add(channel_id)
                log.warning('the bot cannot see channel %d: give it View Channel, Send Messages, Embed Links, Attach Files and Read Message History there, then restart the bot. '
                            'What was to be posted there stays off until then; the commands work.', channel_id)
            except discord.DiscordException as error:
                log.warning('channel %d cannot be reached just now: %s', channel_id, error)
        if found is not None and not hasattr(found, 'send'):
            self.bad_channels.add(channel_id)
            log.warning('%d is a category or another kind of channel that takes no messages: name a text channel and restart the bot.', channel_id)
            return None
        return found

    # -- a sign of life for whatever runs this (the container's health check reads the file's age) ---------------------
    @tasks.loop(seconds=30)
    async def heartbeat(self):
        if self.is_ready() and not self.is_closed():
            import tempfile
            with open(os.path.join(tempfile.gettempdir(), 'satmap-bot.alive'), 'w') as out:
                out.write(str(int(time.time())))

    # -- the line under the bot's name ------------------------------------------------------------------------------
    @tasks.loop(seconds=60)
    async def presence_loop(self):
        text = await asyncio.to_thread(data.presence, self.sm)
        await self.change_presence(activity=discord.CustomActivity(name=text[:128]))

    @presence_loop.before_loop
    async def before_presence(self):
        await self.wait_until_ready()

    # -- the live board: one message, kept up to date ----------------------------------------------------------------
    @tasks.loop(seconds=60)
    async def board_loop(self):
        channel = await self.channel(self.cfg['board']['channel'])
        if channel is None:
            return
        status = await asyncio.to_thread(data.answer, self.sm, 'status')
        alerts = await asyncio.to_thread(data.answer, self.sm, 'alerts')
        embed, _ = render(status, badges=False)
        if alerts['lines'] and alerts['badge'] != 'cleared' and alerts['colour'] != data.QUIET:
            embed.add_field(name=alerts['title'], value='\n'.join(alerts['lines'][:8])[:1024], inline=False)
        embed.set_footer(text='Live board  ·  kept up to date')
        message_id = self.memory.get('board')
        if not message_id and not self.board_looked:
            # A restart that lost the memory (a container with nothing kept): the board is this bot's own last
            # message in that channel with the board's footer. Found again, it is edited, not posted a second time.
            self.board_looked = True
            try:
                async for old in channel.history(limit=50):
                    if old.author.id == self.user.id and old.embeds and (old.embeds[0].footer.text or '').startswith('Live board'):
                        message_id = old.id
                        self.memory.set('board', message_id)
                        break
            except discord.DiscordException as error:
                log.info('board: the channel\'s history cannot be read (%s); a new board message is posted', error)
        try:
            if message_id:
                await channel.get_partial_message(message_id).edit(embed=embed, view=self.map_button())
                return
        except discord.NotFound:
            pass                                                   # somebody deleted it: post a new one
        except discord.DiscordException as error:
            log.warning('board: %s', error)
            return
        try:
            message = await channel.send(embed=embed, view=self.map_button() or discord.utils.MISSING)
            self.memory.set('board', message.id)
        except discord.DiscordException as error:
            log.warning('board: cannot post in channel %d: %s', channel.id, error)

    @board_loop.before_loop
    async def before_board(self):
        await self.wait_until_ready()

    # -- news and alerts ----------------------------------------------------------------------------------------------
    @tasks.loop(seconds=30)
    async def watch_loop(self):
        news_cfg, alert_cfg = self.cfg['news'], self.cfg['alerts']
        news, alert_channel = await self.channel(news_cfg['channel']), await self.channel(alert_cfg['channel'])
        try:
            state = await asyncio.to_thread(self.sm.get, 'state', 20)
            up = True
        except data.Unreachable:
            state, up = None, False
        was_up = self.memory.get('up')
        if news and news_cfg['server'] and was_up is not None and up != was_up:
            card = data.card('The server is up' if up else 'The server is not answering',
                             ['%s is back.' % (state.get('session') or 'The world') if up else 'SatMap stopped answering; the game server may be restarting.'], data.GOOD if up else data.QUIET, 'server' if up else 'leave')
            embed, files = render(card, self.cfg['badges'])
            banner = os.path.join(HERE, 'art', 'satmap-banner.png')
            if up and os.path.isfile(banner):
                files.append(discord.File(banner, filename='satmap-banner.png'))
                embed.set_image(url='attachment://satmap-banner.png')
            await self.post(news, embed, files)
        self.memory.set('up', up)
        if not up:
            return

        names = sorted(p['name'] for p in state.get('players') or (state.get('server') or {}).get('players') or [] if isinstance(p, dict) and p.get('name'))
        before = self.memory.get('players')
        if news and news_cfg['joins'] and before is not None and not self.first_watch:
            for name in sorted(set(names) - set(before)):
                await self.post(news, *render(data.card('%s joined' % name, [data.plural(len(names), 'pioneer') + ' online'], data.GOOD, 'join'), self.cfg['badges']))
            for name in sorted(set(before) - set(names)):
                await self.post(news, *render(data.card('%s left' % name, [data.plural(len(names), 'pioneer') + ' online' if names else 'Nobody is online now'], data.QUIET, 'leave'), self.cfg['badges']))
        self.memory.set('players', names)

        if alert_channel:
            await self.problems_report(alert_channel, alert_cfg, state)
        self.first_watch = False

    @watch_loop.before_loop
    async def before_watch(self):
        await self.wait_until_ready()

    async def problems_report(self, channel, alert_cfg, state):
        """One report for everything that is wrong, edited in place while the trouble lasts (not a message a machine).

        A new message, which is what makes Discord notify, is posted when trouble starts, and again when something
        new goes wrong more than "regroupMinutes" after the last one; everything else is an edit of the report."""
        now = time.time()
        open_now = {a['id']: a for a in (state.get('alerts') or {}).get('list') or [] if a.get('id') and a.get('sev', 0) >= int(alert_cfg['minSeverity'])}
        incident = self.memory.get('incident')
        if incident is None and self.first_watch:
            incident = await self.adopt_report(channel)
        if incident is None:
            if not open_now:
                return
            incident = {'message': 0, 'posted': 0, 'started': now, 'seen': {}, 'cleared': {}}
        seen, cleared = incident['seen'], incident['cleared']
        fresh = [i for i in open_now if i not in seen or i in cleared]
        gone = [i for i in seen if i not in open_now and i not in cleared]
        for i in open_now:
            seen[i] = open_now[i]
            cleared.pop(i, None)
        for i in gone:
            cleared[i] = now
        changed = bool(fresh or gone) or not incident['message']
        if not changed:
            return
        done = [(seen[i], t) for i, t in cleared.items()] if alert_cfg['cleared'] else []
        card = data.problems(self.sm, list(open_now.values()), done, incident['started'], now)
        embed, files = render(card, self.cfg['badges'])
        worst = max((a.get('sev', 0) for a in open_now.values()), default=0)
        again = bool(fresh) and incident['message'] and now - incident['posted'] > 60 * float(alert_cfg['regroupMinutes'])
        if not incident['message'] or again:
            if again:                                             # the older report stays as the record of what it showed
                await self.edit_report(channel, incident['message'], note='Carried on in a newer report below.')
            mention = alert_cfg['mention'] if alert_cfg['mention'] and worst >= int(alert_cfg['mentionSeverity']) else None
            message = await self.post(channel, embed, files, content=mention)
            incident['message'], incident['posted'] = (message.id if message else 0), now
            if again:
                incident['cleared'] = {}
                incident['seen'] = dict(open_now)
        else:
            await self.edit_report(channel, incident['message'], embed, files)
        if not open_now:
            log.info('alerts: all clear after %s', data.ago(now - incident['started']))
            self.memory.set('incident', None)
        else:
            self.memory.set('incident', incident)

    async def edit_report(self, channel, message_id, embed=None, files=None, note=None):
        try:
            message = channel.get_partial_message(message_id)
            if embed is None:                                     # only a closing line under an older report
                old = await channel.fetch_message(message_id)
                if not old.embeds:
                    return
                embed, files = old.embeds[0], []
                embed.set_footer(text=note)
                # Its pictures are the message's own attachments: point at them again, or Discord shows them large.
                names = {a.filename for a in old.attachments}
                if 'satmap.png' in names:
                    embed.set_author(name='SatMap', icon_url='attachment://satmap.png')
                badge = next((n for n in sorted(names) if n != 'satmap.png'), None)
                if badge:
                    embed.set_thumbnail(url='attachment://' + badge)
                await message.edit(embed=embed)
                return
            # New pictures replace the old ones, so the report never carries a picture it does not show.
            await message.edit(embed=embed, attachments=files)
        except discord.DiscordException as error:
            log.info('alerts: the report %s could not be edited: %s', message_id, error)

    async def adopt_report(self, channel):
        """After a restart with nothing remembered: take up the bot's own last open report instead of posting another."""
        try:
            async for old in channel.history(limit=30):
                if old.author.id == self.user.id and old.embeds and (old.embeds[0].footer.text or '').startswith('Problems · worst first'):
                    log.info('alerts: carrying on with the report of %s', old.created_at.strftime('%H:%M'))
                    return {'message': old.id, 'posted': old.created_at.timestamp(), 'started': old.created_at.timestamp(), 'seen': {}, 'cleared': {}}
        except discord.DiscordException as error:
            log.info('alerts: the channel\'s older messages could not be read: %s', error)
        return None

    async def post(self, channel, embed, files, content=None):
        if channel is None:
            return None
        try:
            return await channel.send(content=content, embed=embed, files=files, view=self.map_button() or discord.utils.MISSING,
                                      allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=True))
        except discord.DiscordException as error:
            log.warning('cannot post in channel %d: %s', channel.id, error)
            return None


def check(cfg):
    """--check: everything short of logging in"""
    sm = data.SatMap(cfg['satmap'], timeout=float(cfg['timeoutSeconds']))
    print('settings read: SatMap at %s; commands %s; board %s; news %s; alerts %s' % (
        cfg['satmap'], 'in server %d' % cfg['guild'] if cfg['guild'] else 'global', cfg['board']['channel'] or 'off', cfg['news']['channel'] or 'off', cfg['alerts']['channel'] or 'off'))
    missing = [b for b in sorted({c for c in ('join', 'leave', 'server', 'phase', 'digest', 'todo', 'floor', 'cleared', 'power', 'fuse', 'battery', 'fuel', 'full', 'extractor')})
               if not os.path.isfile(os.path.join(HERE, 'art', 'badges', b + '.png'))]
    print('badges: %s' % ('all there' if not missing else 'MISSING ' + ', '.join(missing)))
    bad = 0
    for name in data.COMMANDS:
        card = data.answer(sm, name)
        embed, files = render(card, cfg['badges'])
        size = len(embed)
        problem = 'TOO LONG' if size > 6000 else 'not answered' if card['title'] in ('That did not work', 'The map\'s server is not answering') else ''
        bad += 1 if problem else 0
        print('  /%-11s %-44s %4d characters, %d fields, badge %s %s' % (name, card['title'][:44], size, len(embed.fields), card['badge'], problem))
    try:
        rows = ((sm.get('state') or {}).get('alerts') or {}).get('list') or []
        report = data.problems(sm, [r for r in rows if r.get('sev', 0) >= int(cfg['alerts']['minSeverity'])])
        embed, files = render(report, cfg['badges'])
        print('  %-12s %-44s %4d characters, badge %s %s' % ('the report', report['title'][:44], len(embed), report['badge'], 'TOO LONG' if len(embed) > 6000 else ''))
        bad += 1 if len(embed) > 6000 else 0
    except data.Unreachable:
        pass
    print('the line under the bot\'s name: %s' % data.presence(sm))
    return bad


class NoVoiceNoise(logging.Filter):
    """discord.py warns twice at every start that voice is not installed; this bot has no voice"""
    def filter(self, record):
        return 'voice will NOT be supported' not in record.getMessage()


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    logging.getLogger('discord.client').addFilter(NoVoiceNoise())
    args = sys.argv[1:]
    path = args[args.index('--config') + 1] if '--config' in args else os.path.join(HERE, 'config.json')
    in_environment = bool(os.environ.get('DISCORD_BOT_TOKEN') or os.environ.get('DISCORD_TOKEN'))
    if not os.path.isfile(path):
        if '--check' in args or in_environment:
            path = os.path.join(HERE, 'config.example.json')         # the defaults; the environment fills in the rest
        else:
            sys.exit('No %s and no DISCORD_BOT_TOKEN in the environment. Copy config.example.json to config.json and fill it in, or set the variables (README.md lists them).' % path)
    cfg = load_config(path)
    used = apply_environment(cfg)
    if used:
        log.info('from the environment: %s', ', '.join(used))
    if '--url' in args:
        cfg['satmap'] = args[args.index('--url') + 1]
    if '--check' in args:
        sys.exit(1 if check(cfg) else 0)
    token = read_token(cfg)
    if not token or token.startswith('PASTE'):
        sys.exit('No bot token: set DISCORD_BOT_TOKEN, or put it in config.json ("token") or in the file "tokenFile" names.')
    bot = SatMapBot(cfg, Memory(memory_path(os.path.dirname(path))))
    bot.run(token, log_handler=None)


if __name__ == '__main__':
    main()
