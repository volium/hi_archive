import click
import json
from xml.etree import ElementTree


@click.command()
@click.argument("input_rss", type=click.Path(exists=True))
def main(input_rss):
    """
    Main function
    Reads input_rss and saves the episode information to a dictionary, which
    is then printed as json.
    """

    rss = ElementTree.parse(input_rss)
    episodes_rss = rss.getroot().findall("./channel/item")

    episodes = []

    for index, episode in enumerate(reversed(episodes_rss), start=1):
        episodes.append(
            {
                "index": index,
                "title": episode.find("title").text,
                "url": episode.find("link").text,
            }
        )

    print(json.dumps(episodes, indent=2))


if __name__ == "__main__":
    main()
