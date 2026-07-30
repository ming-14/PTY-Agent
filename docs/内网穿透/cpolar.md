# cpolar 内网穿透

## 下载
https://www.cpolar.com/download

## 认证
执行`./cpolar authtoken YOUR_TOKEN`

## 启动命令
`./cpolar tcp 18766`

## 注意
cpolar的http不支持websocket，使用请使用tcp转发
使用tcp转发时，请在守护进程配置好证书或者用tsl包装，否则只能使用ws，不能使用wss，也就是说https网页无法连接
