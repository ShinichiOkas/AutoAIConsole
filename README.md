# AutoAIConsole

子プロセス（PTY）で CLI 型 Coding Agent を動作させ、Ollama 上の AI（既定: `gemma4:31b-cloud`）がユーザーに代わってターミナルを監視・操作し、Coding Agent の問いかけに自律的に回答して目的を達成するデスクトップ GUI アプリ。

## コンセプト

- **三者協調**: ユーザー（目的・方針） ⇄ **AutoAI**（自律的ピア / Ollama `gemma4:31b-cloud`） ⇄ **CLI Coding Agent**（子プロセス PTY）
- **自律ファースト**: 通常の問いかけには AutoAI が目的に沿って自動応答し、不可逆操作・目的逸脱・価値判断の境界のみ自律停止してユーザーに相談する
- **ライブ可視化 & 直接介入**: ターミナル出力・監視状態・自動応答の判断根拠をリアルタイム表示しつつ、ユーザーはいつでもターミナルに直接入力できる

## セットアップと起動

```bat
install.bat
launch_gui.bat
```

ブラウザで開く場合:

```bat
launch_gui.bat --browser
```

## ドキュメント

- [UX 設計書](docs/ux_design.md)
