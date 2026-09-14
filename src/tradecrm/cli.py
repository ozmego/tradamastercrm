import typer

from tradecrm.logging_conf import setup_logging

app = typer.Typer(help="TradeCRM - Binance spot trading botu")

backtest_app = typer.Typer(help="Backtest komutlari")
live_app = typer.Typer(help="Canli/paper calistirma komutlari")
risk_app = typer.Typer(help="Risk yonetimi komutlari")
telegram_app = typer.Typer(help="Telegram bildirim komutlari")
news_app = typer.Typer(help="Haber-sentiment komutlari")
dashboard_app = typer.Typer(help="Dashboard komutlari")

app.add_typer(backtest_app, name="backtest")
app.add_typer(live_app, name="live")
app.add_typer(risk_app, name="risk")
app.add_typer(telegram_app, name="telegram")
app.add_typer(news_app, name="news")
app.add_typer(dashboard_app, name="dashboard")


@app.callback()
def main() -> None:
    setup_logging()


if __name__ == "__main__":
    app()
