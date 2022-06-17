import discord
from discord import app_commands
from discord.ext import commands

import spotipy
from spotipy import SpotifyClientCredentials

import asyncio, subprocess, os

env = []
with open("env", "r") as infile:
    for line in infile:
        env.append(str(line).strip())

client_creds = SpotifyClientCredentials(client_id=env[1], client_secret=env[2])
spotify_client = spotipy.Spotify(client_credentials_manager=client_creds)

class MyCog(commands.GroupCog, name="ongaku"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @commands.command()
    async def sync(self, message):
        try:
            await self.bot.tree.sync(guild=discord.Object(id=514188804433641472))
        except:
            await message.send("Something went wrong while syncing commands.")
        else:
            await message.send("Commands have been synced!")

    @app_commands.command(name="download")
    async def parrot(self, interaction: discord.Interaction, url: str) -> None:
        await interaction.response.send_message(f"Link received. Downloading...")
        return_val = subprocess.call(["spotdl", url])
        await interaction.response.send_message(f"I've completed the download. Return code: {return_val}")

    @app_commands.command(name="info")
    async def info(self, interaction: discord.Interaction, url: str) -> None:
        await interaction.response.send_message(f"Link received. Parsing playlist data...")
        try:
            playlist_data = spotify_client.playlist_tracks(url)
            playlist_name = spotify_client.playlist(url)["name"]

            if(os.path.exists(f"./playlists/{playlist_name}")):
                return await interaction.channel.send("I already have a directory with that name! Have you previously downloaded this playlist?")
            os.mkdir(f"./playlists/{playlist_name}")

            songs = []
            for item in playlist_data["items"]:
                artist_text = ""
                for artist in item["track"]["artists"]:
                    artist_text += artist["name"] + ", "
                artist_text = artist_text[:-2]

                songs.append([item["track"]["name"], artist_text, item["track"]["external_urls"]["spotify"]])

            output_string = f"Downloading songs from *{playlist_name}*... \n \n"
            for song in songs:
                output_string += ":arrow_down: " + song[0] + " - " + song[1] + "\n"

            send_message = await interaction.channel.send(output_string)

            for index, song in enumerate(songs):
                await interaction.channel.typing()
                return_val = subprocess.call(["spotdl", "-o", f"./playlists/{playlist_name}", song[2]])
                if return_val == 0:
                    text_array = send_message.content.split("\n")
                    text_array[index + 2] = ":white_check_mark: " + song[0] + " - " + song[1]
                    updated_msg = ""
                    for line in text_array:
                        updated_msg += line + "\n"
                    send_message = await send_message.edit(content=updated_msg)

            await interaction.channel.send("Done!")

        except Exception as e:
            await interaction.channel.send(f"Something went wrong. Error: `{e}`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MyCog(bot), guild=discord.Object(id=514188804433641472))

######################################################

async def main():
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", description="Bot is in testing!", intents=intents)

    async with bot:
        @bot.event
        async def on_ready():
            print("~~~~~~~~~~~")
            print("Logged in as")
            print(bot.user.name)
            print(bot.user.id)
            print("~~~~~~~~~~~")

        await setup(bot)
        await bot.start(env[0].strip())

asyncio.run(main())