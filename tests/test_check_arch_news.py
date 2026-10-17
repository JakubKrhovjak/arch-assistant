from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Arch Linux: Recent news updates</title>
<item><title>Mkinitcpio &gt;=42 requires manual intervention for TPM2-based unlocking</title>
<link>https://archlinux.org/news/mkinitcpio-42/</link>
<description>&lt;p&gt;Starting with mkinitcpio 42 the systemd hook changed.&lt;/p&gt;</description>
<pubDate>{d1}</pubDate></item>
<item><title>New mirror in Prague</title><link>https://archlinux.org/news/mirror/</link>
<description>&lt;p&gt;Nothing to do.&lt;/p&gt;</description><pubDate>{d2}</pubDate></item>
</channel></rss>
"""


def _write_feed(state_dir, days_ago):
    now = datetime.now(timezone.utc)
    (state_dir / "arch-news.xml").write_text(RSS.format(
        d1=format_datetime(now - timedelta(days=days_ago)),
        d2=format_datetime(now - timedelta(days=days_ago))))


def test_arch_news_manual_intervention_recent(check_runner):
    _write_feed(check_runner.state_dir, 3)
    f = check_runner.run("arch-news", {"AA_NEWS_OFFLINE": "1"})
    assert f.severity == "high"
    assert len(f.items) == 1
    assert f.items[0]["title"].startswith("Mkinitcpio >=42 requires manual intervention")
    assert f.items[0]["packages"] == ["mkinitcpio"]
    assert f.summary.startswith("Mkinitcpio")
    assert f.actions == ()


def test_arch_news_old_items_ignored(check_runner):
    _write_feed(check_runner.state_dir, 60)
    f = check_runner.run("arch-news", {"AA_NEWS_OFFLINE": "1"})
    assert f.severity == "none"


def test_arch_news_without_cache_offline_is_none(check_runner):
    f = check_runner.run("arch-news", {"AA_NEWS_OFFLINE": "1"})
    assert f.severity == "none"
