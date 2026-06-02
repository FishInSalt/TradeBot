# Contributing to TradeBot

Thanks for your interest in contributing! This project is an open research
framework, and contributions of all kinds — code, docs, bug reports, ideas —
are welcome.

> 中文版在下方 · Chinese version below.

## License of contributions

By contributing, you agree that your contributions are licensed under the
project's [Apache License 2.0](LICENSE), the same license that covers the
project (inbound = outbound). **There is no separate Contributor License
Agreement (CLA) to sign.**

## Developer Certificate of Origin (DCO)

We use the [Developer Certificate of Origin](https://developercertificate.org/)
instead of a CLA. The DCO is a lightweight statement that you wrote the code (or
otherwise have the right to submit it) and that you agree to license it under
the project's license.

To certify this, simply **sign off** every commit:

```bash
git commit -s -m "your message"
```

The `-s` flag appends a line to your commit message:

```
Signed-off-by: Your Name <your.email@example.com>
```

The name and email must match your `git config user.name` / `user.email`. That
sign-off is your agreement to the DCO (full text below).

## Submitting changes

1. Fork the repo and create a feature branch (don't commit to `main`).
2. Make your change with tests. The suite runs with `pytest`.
3. Sign off your commits (`git commit -s`).
4. Open a Pull Request describing the change and why.

Please keep PRs focused and include tests for behavioral changes.

## Risk note

TradeBot trades real markets when connected to a live exchange. Contributions
that touch order execution, risk handling, or exchange integration get extra
scrutiny. This software is provided **as is** for research/educational use and
is **not financial advice** — see the [README](README.md) disclaimer.

---

# 贡献指南（中文）

感谢你有兴趣贡献！本项目是一个开放的研究框架，欢迎任何形式的贡献——代码、文档、
缺陷报告、想法都欢迎。

## 贡献的授权

提交贡献即表示你同意：你的贡献以本项目的 [Apache License 2.0](LICENSE) 授权，
与项目本身的 license 一致（inbound = outbound）。**无需另行签署贡献者许可协议
（CLA）。**

## 开发者原产地证明（DCO）

我们用 [DCO](https://developercertificate.org/) 代替 CLA。DCO 是一个轻量声明：
你确认这段代码是你写的（或你有权提交），并同意以本项目的 license 授权它。

做法很简单——给每个 commit **加上 sign-off**：

```bash
git commit -s -m "你的提交信息"
```

`-s` 会在提交信息末尾追加一行：

```
Signed-off-by: Your Name <your.email@example.com>
```

其中名字和邮箱需与你的 `git config user.name` / `user.email` 一致。这一行
sign-off 即代表你同意下方的 DCO 全文。

## 提交流程

1. Fork 仓库并创建 feature 分支（不要直接提交到 `main`）。
2. 带测试地完成改动，测试用 `pytest` 运行。
3. 给 commit 加 sign-off（`git commit -s`）。
4. 发起 Pull Request，说明改了什么、为什么。

请保持 PR 聚焦，并为行为变更附上测试。

## 风险提示

连接到实盘交易所时，TradeBot 会进行真实交易。涉及下单执行、风控、交易所接入的
贡献会受到额外审查。本软件按**现状（as is）**提供、仅供研究与教育用途，**不构成
投资建议**——详见 [README](README.md) 免责声明。

---

## Developer Certificate of Origin 1.1

```
By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```
