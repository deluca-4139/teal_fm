import discord
from discord import app_commands
from discord.ext import commands

import youtube_dl

import asyncio, json, random, os, traceback
from async_timeout import timeout

env = []
with open("env_am", "r") as infile:
    for line in infile:
        env.append(str(line).strip())


ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',  # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpeg_options = {
    'options': '-vn',
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    # This class has been taken and modified from the discord.py example at
    # https://github.com/Rapptz/discord.py/blob/master/examples/basic_voice.py
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)

        self.data = data

        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class Player:
    # This class has been taken and modified from the Eviee example at
    # https://gist.github.com/EvieePy/ab667b74e9758433b3eb806c53a19f34
    def __init__(self, ctx, bot):
        # Note that ctx in this instance is likely a discord.Interaction,
        # since this bot is written with the updated slash commands in mind.
        self.bot = bot
        self.guild = ctx.guild
        self.channel = ctx.channel
        self.ctx = ctx
        self.local = False

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()
        self.now_playing = None
        self.playlist_name = ""

        self.volume = 0.5
        self.song_list = []

        self.metadata = {}

        # This will likely break if you switch to 3.10 in the future
        bot.loop.create_task(self.player_loop())

    async def clear_queue(self):
        # We need to confirm this value is false,
        # otherwise the queue will never be empty
        self.endless = False

        while not self.queue.empty():
            await self.queue.get()

        self.song_list = []
    
    async def start_playlist(self, target: str, interaction: discord.Interaction, shuffle: bool, endless: bool):
        self.ctx = interaction
        self.endless = endless
        await self.clear_queue()

        # TODO: fix for playlist order
        song_list = os.listdir(f"./playlists/{target}")
        if shuffle:
            random.shuffle(song_list)

        paths = []
        for song in song_list:
            if song not in [".spotdl-cache", "failed_songs.txt", "metadata.json"]:
                await self.queue.put(f"./playlists/{target}/{song}")
                paths.append(f"./playlists/{target}/{song}")

                index = len(song) - 1
                while song[index] != '.':
                    index -= 1
                self.song_list.append(song[0:index])
        self.playlist_name = target

        metadata_file = open(f"./playlists/{target}/metadata.json", "r")
        self.metadata = json.load(metadata_file)
        metadata_file.close()

    def find_close_keys(self, orig: str):
        keys = []

        orig_list = list(orig)
        max_chars = 1
        while len(keys) == 0:
            for song in self.metadata:
                if abs(len(orig) - len(song) > max_chars):
                    continue
                if song != "tracks":
                    test_list = list(song)
                    for char in orig_list:
                        if char in test_list:
                            test_list.remove(char)
                    if len(test_list) <= max_chars:
                        keys.append([song, len(test_list)])
            max_chars += 1

        return keys     
    
    async def update_embed(self):
        if self.now_playing:
            playing_embed = self.now_playing.embeds[0]
            playing_embed.set_footer(text="Up next: {}".format(self.song_list[1] if (len(self.song_list) > 1) else "nothing"))
            return await self.now_playing.edit(content="", embed=playing_embed, view=PlayerButtons(self.bot))
    
    async def player_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                async with timeout(300):
                    path = await self.queue.get()
                    if type(path) is str:
                        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(path))
                    else:
                        source = path
            except asyncio.TimeoutError:
                # TODO: implement timeout disconnect
                pass

            source.volume = self.volume

            playing_embed = discord.Embed(title=(f"Now Playing ~ {self.playlist_name}" if self.local else "Now Playing"), description=self.song_list[0])
            song = None
            if self.local:
                if self.song_list[0] in self.metadata:
                    song = self.metadata[self.song_list[0]]
                else: 
                    possible_keys = self.find_close_keys(self.song_list[0])
                    if len(possible_keys) == 1:
                        song = self.metadata[possible_keys[0][0]]
                    else:
                        closest = possible_keys[0]
                        for key in possible_keys: 
                            if key[1] < closest[1]:
                                closest = key 
                        song = self.metadata[closest[0]] 
            else:
                song = {
                    'url': self.metadata[self.song_list[0]]['webpage_url'],
                    'album_art': self.metadata[self.song_list[0]]['thumbnail']
                }

            playing_embed.add_field(name="Link:", value=f"[{'Spotify' if self.local else 'YouTube'}]({song['url']})")
            playing_embed.set_footer(text="Up next: {}".format(self.song_list[1] if (len(self.song_list) > 1) else "nothing"))
            playing_embed.set_image(url=song["album_art"])

            # TODO: the view might time out if the song
            # that's playing is longer than 15 minutes.
            # Will need to figure out how to pass
            # timeout=None into the view when creating
            if self.now_playing is None:
                self.now_playing = await self.guild.voice_client.channel.send(content="", embed=playing_embed, view=PlayerButtons(self.bot))
            else:
                await self.now_playing.edit(content="", embed=playing_embed, view=PlayerButtons(self.bot))

            self.guild.voice_client.play(source, after=lambda e: print(f'Player error: {e}') if e else self.bot.loop.call_soon_threadsafe(self.next.set))
            await self.next.wait()

            try:
                self.song_list.pop(0)
            except IndexError:
                pass

            try:
                await self.now_playing.edit(content="", embed=None) # Embed might not update properly if we don't clear it first
            except discord.errors.NotFound:
                pass

class PlayerButtons(discord.ui.View):
    def __init__(self, bot):
        self.bot = bot
        super().__init__()

    def reset_buttons(self):
        for child in self.children:
            child.style = discord.ButtonStyle.primary

    def get_current_voice(self, interaction: discord.Interaction):
        for voice in self.bot.voice_clients:
            if voice.guild.id == interaction.guild.id:
                return voice
        return None

    def check_roles(self, interaction: discord.Interaction, role: int) -> bool:
        if role in [role.id for role in interaction.user.roles]:
            return True
        else:
            return False

    @discord.ui.button(label="", emoji="⏸️", style=discord.ButtonStyle.primary)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_roles(interaction, int(env[1])):
            return await interaction.response.edit_message(view=self)

        current_voice = self.get_current_voice(interaction)

        if current_voice is not None:
            if not current_voice.is_paused():
                self.reset_buttons()
                current_voice.pause()
                button.style = discord.ButtonStyle.success
                return await interaction.response.edit_message(view=self)

        return await interaction.response.edit_message(view=self)

    @discord.ui.button(label="", emoji="▶️", style=discord.ButtonStyle.success)
    async def play(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_roles(interaction, int(env[1])):
            return await interaction.response.edit_message(view=self)

        current_voice = self.get_current_voice(interaction)

        if current_voice is not None:
            if current_voice.is_paused():
                self.reset_buttons()
                current_voice.resume()
                button.style = discord.ButtonStyle.success
                return await interaction.response.edit_message(view=self)

        return await interaction.response.edit_message(view=self)

    @discord.ui.button(label="", emoji="⏭️", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_roles(interaction, int(env[1])):
            return await interaction.response.edit_message(view=self)

        current_voice = self.get_current_voice(interaction)

        if current_voice is not None:
            current_voice.stop()

        return await interaction.response.edit_message(view=self)

    @discord.ui.button(label="", emoji="⏹️", style=discord.ButtonStyle.primary)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_roles(interaction, int(env[1])):
            return await interaction.response.edit_message(view=self)

        current_voice = self.get_current_voice(interaction)

        if current_voice is not None:
            current_voice.stop()

            cog = self.bot.get_cog("am")
            await cog.players[interaction.guild.id].clear_queue()
            del cog.players[interaction.guild.id]

        return await interaction.message.delete()

class VoiceCog(commands.GroupCog, name="am"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot 
        self.players = {}
        super().__init__()

    def get_player(self, ctx):
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = Player(ctx, self.bot)
            self.players[ctx.guild.id] = player

        return player

    @commands.command()
    async def sync_am(self, message):
        try:
            for guild in env[2:]:
                await self.bot.tree.sync(guild=discord.Object(id=guild))
        except Exception as e:
            await message.send(f"Something went wrong while syncing commands. Error: `{e}`")
        else:
            await message.send("Commands have been synced!")

    @app_commands.command(name="join", description="Join a voice channel.")
    @app_commands.describe(channel="the voice channel to join, or blank for the channel you're in")
    async def join(self, interaction: discord.Interaction, channel: discord.VoiceChannel=None):
        if not channel:
            if interaction.user.voice:
                try:
                    channel = interaction.user.voice.channel
                except AttributeError:
                    return await interaction.response.send_message("No channel found. Please join a voice channel or specify a valid one.", ephemeral=True)
            else:
                return await interaction.response.send_message("No channel found. Please join a voice channel or specify a valid one.", ephemeral=True)

        if interaction.guild.voice_client in self.bot.voice_clients:
            if interaction.guild.voice_client.channel == channel:
                return
            else:
                await interaction.guild.voice_client.move_to(channel)
                return await interaction.response.send_message(f"Moved to channel {channel}!", ephemeral=True)
        else:
            await channel.connect()
            return await interaction.response.send_message(f"Joined channel {channel}!", ephemeral=True)

    @app_commands.command(name="leave", description="Leave the current voice channel.")
    async def leave(self, interaction: discord.Interaction):
        # TODO: cleanup before leaving if bot is currently playing
        if interaction.guild.voice_client in self.bot.voice_clients:
            await interaction.guild.voice_client.disconnect()
            return await interaction.response.send_message("Left voice!", ephemeral=True)
        else:
            return await interaction.response.send_message("I am not currently in a voice channel!", ephemeral=True)

    @app_commands.command(name="play", description="Play a downloaded playlist.")
    @app_commands.describe(target="the playlist you want to play", shuffle="whether or not to shuffle the playlist", endless="whether you want the playlist to loop")
    async def play(self, interaction: discord.Interaction, target: str, shuffle: bool, endless: bool = False):
        if not interaction.guild.voice_client:
            # TODO: maybe just have the bot join the voice channel?
            return await interaction.response.send_message("I am not currently connected to a voice channel!", ephemeral=True)

        try:
            player = self.get_player(interaction)
            player.local = True
            await player.start_playlist(target, interaction, shuffle, endless)

            return await interaction.response.send_message("Started playing!", ephemeral=True)
        except Exception as e:
            await interaction.channel.send(f"Something went wrong. Error: `{e}`")

    @app_commands.command(name="queue", description="Queue a song for playing.")
    @app_commands.describe(url="URL of the song you'd like to play")
    async def queue(self, interaction: discord.Interaction, url: str):
        if not interaction.guild.voice_client:
            # TODO: maybe just have the bot join the voice channel?
            return await interaction.response.send_message("I am not currently connected to a voice channel!", ephemeral=True)

        try:
            await interaction.response.defer(ephemeral=True)
            player = self.get_player(interaction)
            player.local = False
            data = ytdl.extract_info(url, download=False)
            player.song_list.append(data['title'])
            player.metadata[data['title']] = data
            source = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
            await player.queue.put(source)
            await player.update_embed()

            return await interaction.edit_original_message(content="Song queued.")
        except Exception as e:
            await interaction.channel.send(f"Something went wrong. Error: `{e}`")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceCog(bot), guilds=[discord.Object(id=guild) for guild in env[2:]])

async def main():
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", description="TeaL AM", intents=intents)
    
    async with bot:
        @bot.event
        async def on_ready():
            print("~~~~~~~~~~~~~")
            print("Logged in as:")
            print(bot.user.name)
            print(bot.user.id)
            print("~~~~~~~~~~~~~")

        await setup(bot)
        await bot.start(env[0])

asyncio.run(main())