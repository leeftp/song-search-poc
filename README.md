# 楽曲検索PoC 効果測定キット

LLMの候補生成品質と照合精度を、インフラ構築なしのローカル実行で測定するためのキット。

## ファイル構成

| ファイル | 内容 |
|---|---|
| test_songs_final.csv | 照合用楽曲DB（49,301曲、実在曲、iTunes公開メタデータ由来） |
| test_songs_30k.csv | 30,000曲版（規模比較用。有名曲が一部欠落するので照合DBにはfinal推奨） |
| test_queries.json | テストクエリ40問（指名8/うろ覚え8/ムード8/シーン8/タイアップ8） |
| matcher.py | ペア照合モジュール（正規化→0.6/0.4加重→confidence3段階。本番照合APIと同ロジック） |
| run_eval.py | 評価ハーネス（LLM候補生成→照合→指標集計） |

## 実行手順

```bash
pip install anthropic rapidfuzz
export ANTHROPIC_API_KEY=sk-ant-...

# まず5問で動作確認
python3 run_eval.py --limit 5

# 全40問
python3 run_eval.py

# web検索なし比較（内蔵web検索の効果測定）
python3 run_eval.py --no-web
```

## 測定できる指標

- 照合ヒット率: LLM候補のうちDBにhigh confidenceで照合できた割合（カテゴリ別）
- 正解率: 正解定義済みクエリ（指名・うろ覚え・タイアップ系）で正解が最終候補に入った割合
- 取りこぼし検知: DB未収録曲（Q08, Q37）が正しく棄却されるか
- レイテンシ: LLM呼び出し秒数（5秒予算の検証）
- コスト: 1クエリあたりのAPI課金概算

## 経路比較のやり方

run_eval.pyのcall_llm()を差し替えれば、同一クエリセットで横並び比較できる:
- Claude API直（このキットのデフォルト）
- 社内LLM（OpenAI互換ならエンドポイント差し替えのみ）
- 顧客MCP経由（verify_songsの呼び出し先を実バックエンドに変更）

matcher.pyのSongMatcher.verify_songs()はMCPツールverify_songsと同一インターフェース
（candidates配列→matches+confidence）なので、本番移行時はこの部分を実API呼び出しに
置き換えるだけ。

## 注意

- 楽曲DBはシードアーティスト約350組由来。網羅性は本番DBに劣るため、
  「取りこぼし率」の絶対値は参考値。本番DB接続後に再測定すること
- 歌詞データは含まない（著作物のため）。歌詞利用は顧客DB側の承認済みデータを使うこと
- run_eval.py内のモデル名・価格は変更される可能性があるため実行時に確認

## GitHub公開時の注意

- data/test_songs_final.csv（49,301曲）はiTunes公開メタデータ由来。
  publicリポジトリでの大量再配布は避け、sample_songs_500.csvのみ残して
  フルデータはscripts/collect_songs.pyで各自生成を推奨。
- run_eval.py実行時はANTHROPIC_API_KEYを環境変数で設定（コードに直書きしない）。
