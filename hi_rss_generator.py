import click
import collections
import concurrent.futures
import queue
import requests
import time
from bs4 import BeautifulSoup
from podgen import Podcast, Episode, Media, Person, Category

# Declare a namedtuple to store episode metadata during initial scrape
EpisodeInfo = collections.namedtuple("EpisodeInfo", ["index", "title", "url"])

# Define a queue that will hold the episodes to iterate through
episode_queue = queue.Queue()


def get_episodes(start=1, end=None):
    """
    This function scrapes the main "Hello Internet" website starting from episode
    `start`, WHICH NEEDS TO BE VALID! Some episodes don't have a regular index
    number, so they can't be used to start the scrape. The very first episode (1),
    however, is valid, that's why it's the default.
    """

    episode_index = start
    base_url = f"https://www.hellointernet.fm"
    episode_url = f"https://www.hellointernet.fm/podcast/{episode_index}"

    while (
        episode_index <= end if end else episode_url != None
    ) and episode_url != None:

        episode_page = requests.get(episode_url, headers={"User-agent": "your bot 0.1"})

        if episode_page.status_code != 200:
            print(
                f"Episode {episode_index}: page_status_code = {episode_page.status_code}"
            )
            print(
                f"Episode {episode_index}: page_status_reason = {episode_page.reason}"
            )

        soup = BeautifulSoup(episode_page.content, "html.parser")
        episode_title = soup.find("meta", itemprop="headline")["content"]

        episode_info = EpisodeInfo(episode_index, episode_title, episode_url)
        episode_queue.put(episode_info)

        print(f'Added episode {episode_index}: "{episode_title}" to the queue')

        next_episode = soup.find("a", id="prevLink")
        episode_index += 1
        episode_url = base_url + next_episode["href"] if next_episode else None

    print(
        f"Finished scrapping, submitted {episode_index - start} episodes for processing"
    )


def generate_episode(episode_info):
    """
    This function scrapes a specific url, gathers all the necessary information and
    generates a podgen episode object with it.
    """

    print(
        f'Generating episode {episode_info.index}: "{episode_info.title}" ({episode_info.url})'
    )

    episode_page = requests.get(
        episode_info.url, headers={"User-agent": "your bot 0.1"}
    )

    if episode_page.status_code != 200:
        print(
            f"Episode {episode_info.index}: page_status_code = {episode_page.status_code}"
        )
        print(
            f"Episode {episode_info.index}: page_status_reason = {episode_page.reason}"
        )

    soup = BeautifulSoup(episode_page.content, "html.parser")
    episode_title = soup.find("meta", itemprop="headline")["content"]
    episode_author = soup.find("meta", itemprop="author")["content"]
    episode_link = soup.find("meta", itemprop="url")["content"]
    episode_datePublished = soup.find("meta", itemprop="datePublished")["content"]

    sqs_block_content_object = soup.find(
        "div", class_="sqs-block markdown-block sqs-block-markdown"
    )

    # NOTE: Some episodes don't have a "qs-block markdown-block sqs-block-markdown" item
    # and instead have "sqs-block html-block sqs-block-html", so...
    if sqs_block_content_object is None:
        sqs_block_content_object = soup.find(
            "div", class_="sqs-block html-block sqs-block-html"
        )

    # Grab the episode description using the "undocumented" decode_contents() method,
    # which returns ONLY the data inside the "div" block, which is what we want.
    episode_content = sqs_block_content_object.find(
        "div", class_="sqs-block-content"
    ).decode_contents()

    # Now get the audio object information
    sqs_audio_embed_object = soup.find("div", class_="sqs-audio-embed").attrs
    episode_media_url = sqs_audio_embed_object["data-url"]

    # Let podgen get the episode media information from the server
    episode_media = Media.create_from_server_response(episode_media_url)

    # Now create the actual podgen episode object and populate the fields
    episode = Episode()
    episode.title = episode_title
    episode.authors = [Person(f"{episode_author}")]
    episode.link = episode_link
    episode.summary = episode_content
    episode.publication_date = episode_datePublished
    episode.media = episode_media

    # Use fetch_duration to get the most accurate information
    episode.media.fetch_duration()

    return episode


# Create the Podcast object
podcast = Podcast(
    name="Hello Internet (archive)",
    authors=[Person("CGP Grey"), Person("Brady Haran")],
    description="CGP Grey and Brady Haran talk about YouTube, life, work, whatever.",
    subtitle="CGP Grey and Brady Haran in Conversation.",
    website="http://www.hellointernet.fm/",
    image="https://images.squarespace-cdn.com/content/v1/52d66949e4b0a8cec3bcdd46/1391195775824-JVU9K0BX50LWOKG99BL5/Hello+Internet.003.png",
    category=Category("Education"),
    language="en-US",
    explicit=False,
)


@click.command()
@click.option(
    "-o",
    "--out",
    "rss_file",
    type=str,
    default="rss.xml",
    required=False,
)
@click.option(
    "-m",
    "--max_workers",
    "max_workers",
    type=int,
    default=20,
    required=False,
    help="number of worker threads to use (20 by default)",
)
@click.option(
    "-f",
    "--first",
    "first_episode_index",
    type=int,
    default=1,
    required=False,
    help="index of first episode to parse (needs to resolve to valid url)",
)
@click.option(
    "-l",
    "--last",
    "last_episode_index",
    type=int,
    default=None,
    required=False,
    help="index of last episode to parse",
)
def main(rss_file, max_workers, first_episode_index, last_episode_index):
    """
    Main function
    """

    print(f"Using the following arguments: ")
    print(f"\trss_file = {rss_file}")
    print(f"\tmax_workers = {max_workers}")
    print(f"\tfirst_episode_index = {first_episode_index}")
    print(f"\tlast_episode_index = {last_episode_index}")
    processed_episodes = []
    failed_episodes = []

    # Grab the start time snapshot
    start = time.time()

    # We can use a "with" statement to ensure threads are cleaned up promptly
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:

        # Start the producer thread which sends work in through the queue
        future_to_episode = {
            executor.submit(
                get_episodes, first_episode_index, last_episode_index
            ): "PRODUCER"
        }

        while future_to_episode:
            # check for status of the futures which are currently working
            done, not_done = concurrent.futures.wait(
                future_to_episode,
                timeout=1,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )

            # If there is incoming work, start a new future
            while not episode_queue.empty():

                # Grab an episode_info object from the queue
                episode_info = episode_queue.get()

                # Create a new future with the episode_info object
                future_to_episode[
                    executor.submit(generate_episode, episode_info)
                ] = episode_info

            # Process any completed futures
            for future in done:
                episode_info = future_to_episode[future]
                try:
                    episode_object = future.result()
                except Exception as e:
                    print(f"Episode {episode_info.index} generated an exception: {e}")
                    failed_episodes.append(episode_info)
                else:
                    if isinstance(episode_object, Episode):
                        print(f'Finished processing episode "{episode_object.title}"')
                        processed_episodes.append((episode_info, episode_object))

                # Remove the now completed future
                future_to_episode.pop(future)

    # Since the episodes may have been processed out of order, sort them using the
    # episode index; the lambda allows us to do it in one shot.
    processed_episodes.sort(key=lambda episode: episode[0].index)

    # Add the episodes to the podcast object, iterate in reverse to add latest
    # episodes at the top of the feed.
    for episode in reversed(processed_episodes):
        podcast.episodes.append(episode[1])

    # Generate the actuall rss feed file
    podcast.rss_file(rss_file, minimize=False)

    # Grab the end time snapshot
    end = time.time()

    # Log the results
    print(f"Generated RSS feed ({rss_file}) in {(end - start):.2f} seconds")

    print(
        f"{len(processed_episodes)} episodes were succesfully processed: \n"
        + "\n".join(
            "\tEpisode {index}: {title}".format(**episode[0]._asdict())
            for episode in processed_episodes
        )
    ) if len(processed_episodes) > 0 else None

    print(
        f"{len(failed_episodes)} episodes failed to be processed: \n"
        + "\n".join(
            "\tEpisode {index}: {title}".format(**episode._asdict())
            for episode in failed_episodes
        )
    ) if len(failed_episodes) > 0 else None


if __name__ == "__main__":
    main()
