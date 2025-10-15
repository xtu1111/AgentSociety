import { Divider, Flex, Layout } from "antd";
import { Content, Header } from "antd/es/layout/layout";
import RootMenu from "./Menu";
import { Link } from "react-router-dom";
import React, { useEffect, useRef } from "react";

export default function RootLayout({
    children,
    selectedKey,
    homePage,
}: {
    children: React.ReactNode
    selectedKey: string
    homePage?: boolean
}) {
    const headerRef = useRef<HTMLDivElement>(null);
    const contentRef = useRef<HTMLDivElement>(null);

    const headerStyle = {
        background: '#000088',
        color: 'white',
    }

    const menuStyle = {
        background: '#000088',
        color: 'white',
    }

    // get the height of the header to set the content height
    useEffect(() => {
        if (!contentRef.current) {
            return
        }

        const updateContentHeight = () => {
            if (!contentRef.current) {
                return
            }

            const headerHeight = headerRef.current?.clientHeight
            if (typeof headerHeight === 'number' && headerHeight > 0) {
                contentRef.current.style.height = `calc(100vh - ${headerHeight}px)`
            } else {
                contentRef.current.style.height = '90vh'
            }
        }

        updateContentHeight()
        window.addEventListener('resize', updateContentHeight)
        return () => {
            window.removeEventListener('resize', updateContentHeight)
        }
    }, []);

    const baseContentStyle: React.CSSProperties = {
        flex: '1 1 auto',
        overflowY: 'auto',
    }

    const contentStyle: React.CSSProperties = homePage ? {
        ...baseContentStyle,
        width: "100vw",
        background: '#000088',
        top: 0,
        left: 0,
        alignContent: "center",
        justifyContent: "center",
    } : {
        ...baseContentStyle,
    }

    return (
        <Layout style={{ height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <Header ref={headerRef} style={headerStyle}>
                <Flex gap='small' align='center' style={{ width: '100%' }}>
                    <Link to="/" style={{ display: 'flex', alignItems: 'center' }}>
                        <img src="/logo.png" alt="AgentSociety" style={{ height: '24px', display: 'block' }} />
                    </Link>
                    <Divider type="vertical" />
                    <div style={{ flex: 1 }}>
                        <RootMenu selectedKey={homePage ? "" : selectedKey} style={menuStyle} />
                    </div>
                </Flex>
            </Header>
            <Content ref={contentRef} style={contentStyle}>
                {children}
            </Content>
        </Layout>
    )
}
