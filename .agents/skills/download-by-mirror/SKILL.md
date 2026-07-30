---
name: download-by-mirror
description: Used when downloading resources from **GitHub**, **npm/pnpm**, **pip**, **WinGet**, **apt**, **DockerHub**, **Maven**, **Cargo**, **Hugging Face**, **nuget** and others, to obtain information on how to use mirror sources under network-restricted conditions.
---

由于中国大陆网络问题，请使用镜像站下载所有可能无法访问的资源

# 注意

**⚠安全警告⚠ 不要在镜像源输入/传入任何敏感信息**
**⚠绝对不允许修改全局配置，只能修改项目或临时配置**
**⚠禁止执行`git config --global url.xxx.insteadOf`，这会修改所有配置，Token会被发送至对应站点！***

## Github 下载

请使用镜像站下载Github资源：

- `https://v4.gh-proxy.org/{Github链接}`，可下载分支源码，raw文件，Release源码，Release文件，gist，api，git
- `https://ghproxy.com/{Github链接}`，可下载分支源码，raw文件，Release源码，Release文件
- `https://gh.llkk.cc/{Github链接}`，可下载分支源码，Release源码，Release文件
- `https://gh.jasonzeng.dev/{Github链接}`，可下载分支源码，Release源码，Release文件

例子：`https://v4.gh-proxy.org/https://github.com/{user}/{repo}/archive/refs/tags/{tag}.zip` — 下载源码包

如果失败了，请检查对应资源是否是404，404的镜像站也下载不了

不要把token传到镜像站

## npm/pnpm 下载

1. 永久切换镜像（请在执行前询问用户）：
	- `npm config set registry https://registry.npmmirror.com/`
2. 临时指定镜像（推荐）：
	- `npm install express --registry=https://registry.npmmirror.com/`

镜像源：
	- `https://registry.npmmirror.com/`
	- `https://mirrors.huaweicloud.com/repository/npm/`
	- `https://mirrors.cloud.tencent.com/npm/`
	- `https://mirrors.163.com/npm/`

## pip 下载

1. 临时使用镜像源
	- `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple {包名}`

镜像源：
	- 清华：`https://pypi.tuna.tsinghua.edu.cn/simple/`
	- 阿里云：`https://mirrors.aliyun.com/pypi/simple/`
	- 中国科技大学：`https://pypi.mirrors.ustc.edu.cn/simple/`
	- 华为云： `https://repo.huaweicloud.com/repository/pypi/simple/`
	- 腾讯云：`https://mirrors.cloud.tencent.com/pypi/simple/`
	
## WinGet

（设置镜像源需要管理员权限）
0. 先检查当前镜像源：`winget source list`
1. 第一步先移除默认源：`winget source remove winget`
2. 添加 USTC 镜像源：`winget source add winget https://mirrors.ustc.edu.cn/winget-source --trust-level trusted`

## apt

请使用镜像源：`https://mirror.tuna.tsinghua.edu.cn/help/ubuntu/`

## DockerHub

请从`https://github.com/dongyubin/DockerHub/raw/refs/heads/main/README.md`拉取可用镜像列表

## Maven

镜像源：
	- http://mirrors.cloud.tencent.com/nexus/repository/maven-public/
	- https://maven.aliyun.com/repository/public
	- https://repo.huaweicloud.com/repository/maven/
	- http://mirrors.163.com/maven/repository/maven-public/

## Cargo

镜像源：
	- 阿里云：https://mirrors.aliyun.com/crates.io-index/ （推荐！）
	- 中国科学技术大学：https://mirrors.ustc.edu.cn/crates.io-index
	- 清华大学：https://mirrors.tuna.tsinghua.edu.cn/git/crates.io-index.git
	
`~/.cargo/config.toml`:
```toml
[source.crates-io]
replace-with = "aliyun"

[source.aliyun]
registry = "sparse+https://mirrors.aliyun.com/crates.io-index/"

[net]
git-fetch-with-cli = true
retry = 5
```

### 执行 `cargo check`

该命令需要从 Github 等位置拉取代码，所以需要编译前的临时设置镜像站

```powershell
# 编译前临时设置
$env:GIT_CONFIG_COUNT = "1"
$env:GIT_CONFIG_KEY_0 = "url.https://example.com/https://github.com/.insteadOf"
$env:GIT_CONFIG_VALUE_0 = "https://github.com/"
cargo check
# ⚠⚠⚠ 用完立即清除 ⚠⚠⚠
Remove-Item Env:GIT_CONFIG_COUNT
Remove-Item Env:GIT_CONFIG_KEY_0
Remove-Item Env:GIT_CONFIG_VALUE_0
```
（`https://example.com/`应该是镜像站地址）

## vcpkg

需要配置 GitHub 下载代理

修改 `vcpkg/scripts/cmake/vcpkg_from_github.cmake`：
```cmake
set(github_host "https://example.com/https://github.com")
set(github_api_url "https://example.com/https://api.github.com") # 可选，一般 Github API 被墙概率较小
```

修改 `vcpkg/scripts/cmake/vcpkg_download_distfile.cmake`：
```cmake
foreach(url IN LISTS arg_URLS)
    string(FIND "${url}" "v4.gh-proxy.org" _has_proxy)
    if(_has_proxy LESS 0)
        string(REPLACE "https://github.com" "https://example.com/https://github.com" url "${url}")
    endif()
    vcpkg_list(APPEND params "--url=${url}")
endforeach()
```

修改 `vcpkg/scripts/cmake/vcpkg_from_git.cmake`，googlesource 替换为 GitHub 镜像：
```cmake
string(REPLACE "https://chromium.googlesource.com" "https://github.com/lemenkov" arg_URL "${arg_URL}")
```

（`https://example.com/`应该是镜像站地址）

## Hugging Face

镜像源：
	- https://hf-mirror.com

...其他的资源也同理，尽量使用镜像源

## NuGet

镜像源：
	- 华为云：https://repo.huaweicloud.com/repository/nuget/v3/index.json
